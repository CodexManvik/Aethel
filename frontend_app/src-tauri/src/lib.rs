use std::collections::HashMap;
use std::fs::{self, File};
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

/// Resolve the real interpreter path. Killing the `py` launcher does not kill
/// the python.exe it starts, so we must spawn python.exe directly.
fn python_executable(repo: &Path) -> String {
    if let Ok(p) = std::env::var("AETHEL_PYTHON") {
        return p;
    }
    let venv = repo.join(".venv").join("Scripts").join("python.exe");
    if venv.exists() {
        return venv.to_string_lossy().into_owned();
    }
    if cfg!(windows) {
        if let Ok(out) = Command::new("py")
            .args(["-3.11", "-c", "import sys; print(sys.executable)"])
            .output()
        {
            let path = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if !path.is_empty() {
                return path;
            }
        }
        return "python".into();
    }
    "python3".into()
}

fn spawn_backend(token: &str, port: u16) -> std::io::Result<Child> {
    let repo = repo_root();
    let backend_dir = std::env::var("AETHEL_BACKEND_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| repo.join("backend"));
    let logs = aethel_home().join("logs");
    fs::create_dir_all(&logs)?;
    let log = File::create(logs.join("backend.log"))?;

    let mut cmd = Command::new(python_executable(&repo));
    cmd.args(["-m", "aethel"])
        .current_dir(backend_dir)
        .env("AETHEL_TOKEN", token)
        .env("AETHEL_PORT", port.to_string())
        .env("AETHEL_PARENT_PID", std::process::id().to_string())
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd.spawn()
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port: u16 = std::env::var("AETHEL_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(8765);
    let token = std::env::var("AETHEL_TOKEN").unwrap_or_else(|_| uuid::Uuid::new_v4().simple().to_string());
    let info = BackendInfo { url: format!("http://127.0.0.1:{port}"), token: token.clone() };

    let app = tauri::Builder::default()
        .manage(info)
        .manage(BackendProcess(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![get_backend_info, secret_set, secret_delete, secret_get_all])
        .setup(move |app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default().level(log::LevelFilter::Info).build(),
                )?;
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
