use std::collections::HashMap;
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Manager, RunEvent};

const KEYRING_SERVICE: &str = "com.aethel.app";
const KNOWN_PROVIDERS: [&str; 5] = ["groq", "gemini", "openrouter", "custom", "typesafe"];

#[derive(Clone, Serialize)]
struct BackendInfo {
    url: String,
    token: String,
}

struct BackendProcess(Mutex<Option<Child>>);

fn validate_provider(provider: &str) -> Result<(), String> {
    if KNOWN_PROVIDERS.contains(&provider) {
        Ok(())
    } else {
        Err(format!("unknown provider: {provider}"))
    }
}

fn aethel_home() -> PathBuf {
    if let Ok(home) = std::env::var("AETHEL_HOME") {
        return PathBuf::from(home);
    }
    let base = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_else(|_| ".".into());
    PathBuf::from(base).join(".aethel")
}

fn repo_root() -> PathBuf {
    // Dev layout: <repo>/frontend_app/src-tauri
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// The backend needs its own packages, so a candidate interpreter only counts if
/// it can import them. Otherwise an active conda env (whose `python` comes first
/// on PATH) silently starts the backend under a Python that can't run it.
/// It also reports its real sys.executable: `py`, `python3` and the Python
/// Install Manager's WindowsApps aliases are launchers that start python.exe as
/// a child, and killing a launcher doesn't stop the backend behind it.
const PROBE: &str = "import fastapi, uvicorn, openai, pydantic, yaml, sys; print(sys.executable)";
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

fn quiet(program: &str) -> Command {
    let mut cmd = Command::new(program);
    // A conda or venv activation must not leak its library paths into our Python.
    cmd.env_remove("PYTHONHOME").env_remove("PYTHONPATH");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

/// The real python.exe behind `program args…`, if it can run the backend.
fn resolve_python(program: &str, args: &[&str]) -> Option<String> {
    let out = quiet(program).args(args).args(["-c", PROBE]).stderr(Stdio::null()).output().ok()?;
    let path = String::from_utf8_lossy(&out.stdout).lines().last().unwrap_or("").trim().to_string();
    (out.status.success() && !path.is_empty() && Path::new(&path).exists()).then_some(path)
}

/// Returns the interpreter to spawn (always a real python.exe) and how it was
/// found, or what was tried.
fn python_executable(repo: &Path) -> Result<(String, String), String> {
    let mut candidates: Vec<(String, Vec<&str>, String)> = Vec::new();
    if let Ok(p) = std::env::var("AETHEL_PYTHON") {
        candidates.push((p.clone(), vec![], format!("AETHEL_PYTHON={p}")));
    }
    let venv = repo.join(".venv").join("Scripts").join("python.exe");
    if venv.exists() {
        candidates.push((venv.to_string_lossy().into_owned(), vec![], "the repo's .venv".into()));
    }
    if cfg!(windows) {
        candidates.push(("py".into(), vec!["-3.11"], "py -3.11".into()));
    }
    candidates.push(("python".into(), vec![], "python on PATH".into()));
    candidates.push(("python3".into(), vec![], "python3 on PATH".into()));
    let mut tried = Vec::new();
    for (program, args, how) in candidates {
        if let Some(real) = resolve_python(&program, &args) {
            return Ok((real, how));
        }
        tried.push(how);
    }
    Err(format!(
        "No Python that can run Aethel was found. Tried: {}.\nInstall the packages with:\n    py -3.11 -m pip install -r requirements.txt\nor point AETHEL_PYTHON at a Python that has them.",
        tried.join("; ")
    ))
}

fn spawn_backend(token: &str, port: u16) -> std::io::Result<Child> {
    let repo = repo_root();
    let backend_dir = std::env::var("AETHEL_BACKEND_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| repo.join("backend"));
    let logs = aethel_home().join("logs");
    fs::create_dir_all(&logs)?;
    let mut log = File::create(logs.join("backend.log"))?;
    let (python, how) = match python_executable(&repo) {
        Ok(found) => found,
        Err(why) => {
            writeln!(log, "Aethel could not start its backend.\n\n{why}")?;
            return Err(std::io::Error::new(std::io::ErrorKind::NotFound, why));
        }
    };
    writeln!(log, "Aethel backend log\npython: {python} (found via {how})\nbackend: {}\n", backend_dir.display())?;

    let mut cmd = quiet(&python);
    cmd.args(["-m", "aethel"])
        .current_dir(backend_dir)
        .env("AETHEL_TOKEN", token)
        .env("AETHEL_PORT", port.to_string())
        .env("AETHEL_PARENT_PID", std::process::id().to_string())
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log));
    cmd.spawn()
}

fn backend_log_path() -> PathBuf {
    aethel_home().join("logs").join("backend.log")
}

/// The end of backend.log, for the boot screen when the backend won't start.
#[tauri::command]
fn backend_log_tail(lines: Option<usize>) -> String {
    let text = fs::read_to_string(backend_log_path()).unwrap_or_else(|e| format!("(couldn't read the log: {e})"));
    let all: Vec<&str> = text.lines().collect();
    let n = lines.unwrap_or(60).min(all.len());
    format!("{}\n\n(full log: {})", all[all.len() - n..].join("\n"), backend_log_path().display())
}

/// Open the logs folder with the backend log selected.
#[tauri::command]
fn open_backend_logs() -> Result<(), String> {
    let path = backend_log_path();
    #[cfg(windows)]
    let result = Command::new("explorer").arg(format!("/select,{}", path.display())).spawn();
    #[cfg(target_os = "macos")]
    let result = Command::new("open").arg("-R").arg(&path).spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let result = Command::new("xdg-open").arg(path.parent().unwrap_or(&path)).spawn();
    result.map(|_| ()).map_err(|e| e.to_string())
}

/// Some(exit description) once the backend process has exited (or never started).
#[tauri::command]
fn backend_exited(process: tauri::State<BackendProcess>) -> Option<String> {
    if std::env::var("AETHEL_EXTERNAL_BACKEND").as_deref() == Ok("1") {
        return None; // someone else runs the backend; nothing of ours to watch
    }
    let mut guard = process.0.lock().ok()?;
    match guard.as_mut() {
        None => Some("it didn't start".into()),
        Some(child) => match child.try_wait() {
            Ok(Some(status)) => Some(format!("it stopped ({status})")),
            _ => None,
        },
    }
}

/// Stop the backend if it's running and start it again (the boot screen's Try again).
#[tauri::command]
fn restart_backend(info: tauri::State<BackendInfo>, process: tauri::State<BackendProcess>) -> Result<(), String> {
    let port: u16 = info.url.rsplit(':').next().and_then(|p| p.parse().ok()).unwrap_or(8765);
    let mut guard = process.0.lock().map_err(|e| e.to_string())?;
    if let Some(mut child) = guard.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    *guard = Some(spawn_backend(&info.token, port).map_err(|e| e.to_string())?);
    Ok(())
}

#[tauri::command]
fn get_backend_info(info: tauri::State<BackendInfo>) -> BackendInfo {
    info.inner().clone()
}

#[tauri::command]
fn secret_set(provider: String, value: String) -> Result<(), String> {
    validate_provider(&provider)?;
    keyring::Entry::new(KEYRING_SERVICE, &provider)
        .and_then(|e| e.set_password(&value))
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn secret_delete(provider: String) -> Result<(), String> {
    validate_provider(&provider)?;
    match keyring::Entry::new(KEYRING_SERVICE, &provider).and_then(|e| e.delete_credential()) {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}

#[tauri::command]
fn secret_get_all() -> Result<HashMap<String, String>, String> {
    let mut out = HashMap::new();
    for provider in KNOWN_PROVIDERS {
        let entry = keyring::Entry::new(KEYRING_SERVICE, provider).map_err(|e| e.to_string())?;
        match entry.get_password() {
            Ok(value) => {
                out.insert(provider.to_string(), value);
            }
            Err(keyring::Error::NoEntry) => {}
            Err(e) => return Err(e.to_string()),
        }
    }
    Ok(out)
}

/// The ghost cursor (spec §4.4): a transparent, always-on-top window over the
/// primary monitor that never takes clicks or focus. It's always "shown" but
/// draws nothing until a task points somewhere, so showing it can't steal focus
/// from the app being driven.
fn create_cursor_overlay(app: &mut tauri::App) -> tauri::Result<()> {
    let window = tauri::WebviewWindowBuilder::new(app, "overlay", tauri::WebviewUrl::App("overlay.html".into()))
        .title("Aethel cursor")
        .transparent(true)
        .decorations(false)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .focused(false)
        .focusable(false)
        .visible(false)
        .build()?;
    if let Some(monitor) = window.primary_monitor()? {
        window.set_position(*monitor.position())?;
        window.set_size(*monitor.size())?;
    }
    window.set_ignore_cursor_events(true)?;
    window.show()?;
    Ok(())
}

/// Ctrl+Alt+Esc anywhere cancels every task (spec §4.3). The window turns the
/// event into a `kill_switch` message on its socket.
fn register_kill_switch(app: &mut tauri::App) {
    use tauri::Emitter;
    use tauri_plugin_global_shortcut::{Code, Modifiers, Shortcut, ShortcutState};

    let kill = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::Escape);
    let builder = match tauri_plugin_global_shortcut::Builder::new().with_shortcuts([kill]) {
        Ok(b) => b,
        Err(e) => return log::warn!("kill-switch hotkey unavailable: {e}"),
    };
    let plugin = builder
        .with_handler(move |app, shortcut, event| {
            if shortcut == &kill && event.state() == ShortcutState::Pressed {
                let _ = app.emit("kill-switch", ());
            }
        })
        .build();
    // Another program may already own the hotkey; the Cancel buttons still work.
    if let Err(e) = app.handle().plugin(plugin) {
        log::warn!("kill-switch hotkey unavailable: {e}");
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port: u16 = std::env::var("AETHEL_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(8765);
    let token = std::env::var("AETHEL_TOKEN").unwrap_or_else(|_| uuid::Uuid::new_v4().simple().to_string());
    let info = BackendInfo { url: format!("http://127.0.0.1:{port}"), token: token.clone() };

    let app = tauri::Builder::default()
        .manage(info)
        .manage(BackendProcess(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            get_backend_info, secret_set, secret_delete, secret_get_all, backend_log_tail, open_backend_logs,
            backend_exited, restart_backend
        ])
        .setup(move |app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default().level(log::LevelFilter::Info).build(),
                )?;
            }
            register_kill_switch(app);
            if let Err(e) = create_cursor_overlay(app) {
                log::warn!("ghost cursor overlay unavailable: {e}");
            }
            if std::env::var("AETHEL_EXTERNAL_BACKEND").as_deref() != Ok("1") {
                match spawn_backend(&token, port) {
                    Ok(child) => *app.state::<BackendProcess>().0.lock().unwrap() = Some(child),
                    Err(e) => log::error!("failed to start the Aethel backend: {e}"),
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Aethel");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            // The backend puts llama-server in a kill-on-close job, so this hard
            // kill takes llama-server down with it.
            if let Ok(mut guard) = handle.state::<BackendProcess>().0.lock() {
                if let Some(mut child) = guard.take() {
                    if let Err(e) = child.kill() {
                        log::warn!("failed to stop the Aethel backend: {e}");
                    }
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_known_providers_are_accepted() {
        assert!(validate_provider("groq").is_ok());
        assert!(validate_provider("typesafe").is_ok());
        assert!(validate_provider("../evil").is_err());
    }

    #[test]
    fn aethel_home_honours_env() {
        std::env::set_var("AETHEL_HOME", "C:\\tmp\\aethel-test");
        assert_eq!(aethel_home(), std::path::PathBuf::from("C:\\tmp\\aethel-test"));
        std::env::remove_var("AETHEL_HOME");
    }
}
