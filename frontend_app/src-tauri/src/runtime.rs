//! The installed app's own Python (spec 2026-10-19 §I2). The installer carries the backend's source, a hashed lock
//! of its packages and Astral's uv; on first launch (and whenever the lock changes) uv puts a Python 3.11 and those
//! packages into ~/.aethel/runtime. A dev build (`tauri dev`) never uses any of this: it runs the repo's backend.

use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde::Serialize;

/// Where everything is, for an installed app.
#[derive(Clone, Debug)]
pub struct Layout {
    pub resources: PathBuf,
    pub home: PathBuf,
}

impl Layout {
    pub fn uv(&self) -> PathBuf {
        self.resources.join("uv").join(if cfg!(windows) { "uv.exe" } else { "uv" })
    }
    pub fn lock(&self) -> PathBuf {
        self.resources.join("requirements").join("core.txt")
    }
    pub fn backend_dir(&self) -> PathBuf {
        self.resources.join("backend")
    }
    pub fn runtime(&self) -> PathBuf {
        self.home.join("runtime")
    }
    pub fn venv(&self) -> PathBuf {
        self.runtime().join(".venv")
    }
    pub fn python(&self) -> PathBuf {
        if cfg!(windows) {
            self.venv().join("Scripts").join("python.exe")
        } else {
            self.venv().join("bin").join("python")
        }
    }
    pub fn marker(&self) -> PathBuf {
        self.runtime().join("requirements.sha256")
    }
    pub fn log(&self) -> PathBuf {
        self.home.join("logs").join("setup.log")
    }
    /// Written by the backend once local image support is installed (downloads.py), so a sync can put it back.
    pub fn local_images_marker(&self) -> PathBuf {
        self.runtime().join("local-images.installed")
    }
    /// The environment every uv call (and the backend, for "Install local image support" and uvx) gets.
    pub fn uv_env(&self) -> Vec<(String, String)> {
        vec![
            ("UV_PYTHON_INSTALL_DIR".into(), self.runtime().join("python").to_string_lossy().into_owned()),
            ("UV_CACHE_DIR".into(), self.runtime().join("cache").to_string_lossy().into_owned()),
            ("UV_PYTHON_PREFERENCE".into(), "only-managed".into()), // never whatever Python the PC happens to have
            ("UV_NO_CONFIG".into(), "1".into()),
        ]
    }
}

/// An installed app is a release build whose resources carry the backend.
pub fn installed_layout(resources: Option<PathBuf>, home: PathBuf) -> Option<Layout> {
    if cfg!(debug_assertions) && std::env::var("AETHEL_FORCE_INSTALLED").as_deref() != Ok("1") {
        return None;
    }
    let resources = resources?;
    resources.join("backend").join("aethel").is_dir().then_some(Layout { resources, home })
}

#[derive(Clone, Debug, PartialEq)]
pub enum Step {
    InstallPython,
    CreateVenv,
    SyncPackages,
    RestoreLocalImages,
    Verify,
}

impl Step {
    pub fn label(&self) -> &'static str {
        match self {
            Step::InstallPython => "Getting Python 3.11",
            Step::CreateVenv => "Making Aethel's own environment",
            Step::SyncPackages => "Installing Aethel's packages (about 400 MB, once)",
            Step::RestoreLocalImages => "Putting local image support back",
            Step::Verify => "Checking everything is in place",
        }
    }
}

/// What needs doing. No runtime, or no marker (the last setup never finished: anything in the venv may be half
/// made): everything, from scratch. A new lock (an update of the app): the packages only. Local image support,
/// if the user installed it, is put back after the packages (a sync removes what the lock doesn't list; uv's cache
/// makes it quick). Then a quick import check before the marker is written.
pub fn plan(python_exists: bool, marker: Option<&str>, lock_hash: &str, local_images: bool) -> Vec<Step> {
    let mut steps = if !python_exists || marker.is_none() {
        vec![Step::InstallPython, Step::CreateVenv, Step::SyncPackages]
    } else if marker.map(str::trim) != Some(lock_hash) {
        vec![Step::SyncPackages]
    } else {
        return vec![];
    };
    if local_images {
        steps.push(Step::RestoreLocalImages);
    }
    steps.push(Step::Verify);
    steps
}

/// The program and arguments for a step.
pub fn command_for(step: &Step, layout: &Layout) -> (PathBuf, Vec<String>) {
    let s = |p: PathBuf| p.to_string_lossy().into_owned();
    let uv = layout.uv();
    match step {
        Step::InstallPython => (uv, vec!["python".into(), "install".into(), "3.11".into()]),
        Step::CreateVenv => (uv, vec!["venv".into(), s(layout.venv()), "--python".into(), "3.11".into(), "--clear".into()]),
        Step::SyncPackages => (uv, vec![
            "pip".into(), "sync".into(), "--python".into(), s(layout.python()),
            "--require-hashes".into(), s(layout.lock()),
        ]),
        Step::RestoreLocalImages => (uv, local_images_args(layout)),
        Step::Verify => (layout.python(), vec![
            "-c".into(),
            "import fastapi, uvicorn, openai, numpy, onnxruntime, tokenizers, turbovec, cv2, PIL, yaml".into(),
        ]),
    }
}

/// `uv pip install` of the optional local-images lock: hashed, and constrained by the core lock so it can never
/// change a core package. The backend runs the same command (downloads.py) when the user asks for it.
pub fn local_images_args(layout: &Layout) -> Vec<String> {
    let s = |p: PathBuf| p.to_string_lossy().into_owned();
    vec![
        "pip".into(), "install".into(), "--python".into(), s(layout.python()), "--require-hashes".into(),
        "--index-strategy".into(), "unsafe-best-match".into(), "-r".into(),
        s(layout.resources.join("requirements").join("local-images.txt")), "--constraint".into(), s(layout.lock()),
    ]
}

/// The lock's SHA-256, as hex: the marker that says which lock the runtime was set up from.
pub fn sha256_hex(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(data).iter().map(|b| format!("{b:02x}")).collect()
}

/// What the boot screen shows while the runtime is set up.
#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct SetupStatus {
    pub phase: String, // "checking" | "installing" | "ready" | "failed"
    pub step: String,
    pub done: usize,
    pub total: usize,
    pub error: Option<String>,
}

impl SetupStatus {
    pub fn new(phase: &str) -> Self {
        SetupStatus { phase: phase.into(), step: String::new(), done: 0, total: 0, error: None }
    }
}

/// Runs a step's program; the real one starts it, tests pass a fake.
pub type Runner<'a> = dyn FnMut(&Step, &Path, &[String]) -> Result<(), String> + 'a;

/// Brings the runtime up to date: the steps plan() asks for, then the marker (only once everything, the import
/// check included, has worked). `report` hears each step as it starts.
pub fn ensure(layout: &Layout, run: &mut Runner, report: &mut dyn FnMut(SetupStatus)) -> Result<(), String> {
    let lock = fs::read(layout.lock()).map_err(|e| format!("the package list is missing from the app ({e})"))?;
    let hash = sha256_hex(&lock);
    let marker = fs::read_to_string(layout.marker()).ok();
    let steps = plan(layout.python().is_file(), marker.as_deref(), &hash, layout.local_images_marker().is_file());
    if !steps.is_empty() {
        let _ = fs::remove_file(layout.marker()); // an interrupted run must start from scratch next time
    }
    let total = steps.len();
    for (i, step) in steps.iter().enumerate() {
        report(SetupStatus { phase: "installing".into(), step: step.label().into(), done: i, total, error: None });
        let (program, args) = command_for(step, layout);
        run(step, &program, &args)?;
    }
    if total > 0 {
        fs::create_dir_all(layout.runtime()).map_err(|e| e.to_string())?;
        fs::write(layout.marker(), &hash).map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// A user's own Python settings must not leak into Aethel's runtime (a PYTHONPATH pointing at another Python's
/// packages breaks it).
pub fn clean_python_env(cmd: &mut Command) {
    cmd.env_remove("PYTHONPATH").env_remove("PYTHONHOME").env("PYTHONNOUSERSITE", "1");
}

/// The real runner: the step's program with the runtime's environment, its output appended to setup.log, no console
/// window. The running process's id is kept in `running`, so quitting the app mid-setup can stop it.
pub fn process_runner<'a>(layout: &'a Layout, running: &'a std::sync::Mutex<Option<u32>>)
    -> impl FnMut(&Step, &Path, &[String]) -> Result<(), String> + 'a {
    move |step: &Step, program: &Path, args: &[String]| {
        let log_path = layout.log();
        if let Some(dir) = log_path.parent() {
            let _ = fs::create_dir_all(dir);
        }
        let mut log = File::options().create(true).append(true).open(&log_path).map_err(|e| e.to_string())?;
        let _ = writeln!(log, "\n== {} ==\n> {} {}", step.label(), program.display(), args.join(" "));
        let mut cmd = Command::new(program);
        cmd.args(args).envs(layout.uv_env()).stdin(Stdio::null())
            .stdout(Stdio::from(log.try_clone().map_err(|e| e.to_string())?))
            .stderr(Stdio::from(log.try_clone().map_err(|e| e.to_string())?));
        clean_python_env(&mut cmd);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        }
        let mut child = cmd.spawn().map_err(|e| format!("couldn't run {}: {e}", program.display()))?;
        if let Ok(mut slot) = running.lock() {
            *slot = Some(child.id());
        }
        let status = child.wait();
        if let Ok(mut slot) = running.lock() {
            *slot = None;
        }
        let status = status.map_err(|e| e.to_string())?;
        if status.success() {
            Ok(())
        } else {
            Err(format!("{} failed ({status}). The details are in {}.", step.label(), log_path.display()))
        }
    }
}

/// Stops a setup step that is still running, and what it started (uv starts Python processes of its own).
pub fn stop_tree(pid: u32) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let _ = Command::new("taskkill").args(["/F", "/T", "/PID", &pid.to_string()])
            .creation_flags(0x0800_0000).status();
    }
    #[cfg(not(windows))]
    {
        let _ = Command::new("kill").arg(pid.to_string()).status();
    }
}

pub fn log_tail(path: &Path, lines: usize) -> String {
    let text = fs::read_to_string(path).unwrap_or_default();
    let all: Vec<&str> = text.lines().collect();
    all[all.len().saturating_sub(lines)..].join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn layout(dir: &Path) -> Layout {
        let resources = dir.join("res");
        fs::create_dir_all(resources.join("requirements")).unwrap();
        fs::write(resources.join("requirements").join("core.txt"), "fastapi==0.136.1 --hash=sha256:abc\n").unwrap();
        Layout { resources, home: dir.join("home") }
    }

    fn temp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("aethel-runtime-test-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn sha256_known_vectors() {
        assert_eq!(sha256_hex(b""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
        assert_eq!(sha256_hex(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        let long = vec![b'a'; 1000];
        assert_eq!(sha256_hex(&long), "41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3");
    }

    #[test]
    fn plan_covers_every_case() {
        let full = vec![Step::InstallPython, Step::CreateVenv, Step::SyncPackages, Step::Verify];
        assert_eq!(plan(false, None, "h", false), full);
        assert_eq!(plan(true, None, "h", false), full); // a half-made runtime is rebuilt, not patched
        assert_eq!(plan(true, Some("old"), "h", false), vec![Step::SyncPackages, Step::Verify]);
        assert_eq!(plan(true, Some("old"), "h", true), vec![Step::SyncPackages, Step::RestoreLocalImages, Step::Verify]);
        assert_eq!(plan(true, Some("h\n"), "h", true), vec![]);
    }

    #[test]
    fn first_run_installs_then_writes_the_marker_and_the_second_does_nothing() {
        let dir = temp("first");
        let l = layout(&dir);
        let mut ran = vec![];
        let mut seen = vec![];
        let mut runner = |step: &Step, _program: &Path, args: &[String]| {
            ran.push((step.clone(), args.to_vec()));
            if *step == Step::CreateVenv {
                fs::create_dir_all(l.python().parent().unwrap()).unwrap();
                fs::write(l.python(), "").unwrap();
            }
            Ok(())
        };
        ensure(&l, &mut runner, &mut |s| seen.push(s)).unwrap();
        assert_eq!(ran.len(), 4);
        assert_eq!(ran[2].1[0..2], ["pip".to_string(), "sync".to_string()]);
        assert_eq!(ran[3].0, Step::Verify);
        assert!(ran[2].1.contains(&"--require-hashes".to_string()));
        assert_eq!(seen.iter().map(|s| s.done).collect::<Vec<_>>(), vec![0, 1, 2, 3]);
        let marker = fs::read_to_string(l.marker()).unwrap();
        assert_eq!(marker, sha256_hex(&fs::read(l.lock()).unwrap()));
        let mut again = 0;
        ensure(&l, &mut |_: &Step, _: &Path, _: &[String]| { again += 1; Ok(()) }, &mut |_| {}).unwrap();
        assert_eq!(again, 0);
        // a new lock (an update of the app) syncs the packages only
        fs::write(l.lock(), "fastapi==0.200.0 --hash=sha256:def\n").unwrap();
        let mut steps = vec![];
        ensure(&l, &mut |s: &Step, _: &Path, _: &[String]| { steps.push(s.clone()); Ok(()) }, &mut |_| {}).unwrap();
        assert_eq!(steps, vec![Step::SyncPackages, Step::Verify]);
    }

    #[test]
    fn a_failure_stops_and_leaves_no_marker() {
        let dir = temp("fail");
        let l = layout(&dir);
        let err = ensure(&l, &mut |s: &Step, _: &Path, _: &[String]| {
            if *s == Step::CreateVenv { Err("no network".into()) } else { Ok(()) }
        }, &mut |_| {}).unwrap_err();
        assert_eq!(err, "no network");
        assert!(!l.marker().exists());
    }

    #[test]
    fn local_images_are_constrained_by_the_core_lock_and_hashed() {
        let dir = temp("li");
        let l = layout(&dir);
        let args = local_images_args(&l);
        assert!(args.contains(&"--require-hashes".to_string()));
        let c = args.iter().position(|a| a == "--constraint").unwrap();
        assert_eq!(args[c + 1], l.lock().to_string_lossy());
    }

    #[test]
    fn a_dev_build_never_uses_the_installed_layout() {
        if cfg!(debug_assertions) {
            std::env::remove_var("AETHEL_FORCE_INSTALLED");
            let dir = temp("dev");
            fs::create_dir_all(dir.join("backend").join("aethel")).unwrap();
            assert!(installed_layout(Some(dir.clone()), dir.join("home")).is_none());
        }
    }
}
