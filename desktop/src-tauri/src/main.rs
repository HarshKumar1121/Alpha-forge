//! Alpha Harness on the desktop: the app in a window of its own, an icon in the tray, and the
//! supervisor (see [`supervisor`]) that installs, updates and runs the backend behind them.

// No console window behind the app on Windows; a debug build keeps one for its output.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod supervisor;

use std::sync::Mutex;

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};

/// The step the supervisor is on, for a start-up page that loads after it was announced.
#[derive(Default)]
pub struct Status(pub Mutex<String>);

#[tauri::command]
fn startup_status(window: tauri::WebviewWindow, status: tauri::State<'_, Status>) -> String {
    if let Ok(url) = window.url() {
        supervisor::remember_splash(url);
    }
    status.0.lock().map(|s| s.clone()).unwrap_or_default()
}

/// Bring the window forward: from the tray, a second launch, or the Dock.
pub fn show(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open Alpha Harness", true, None::<&str>)?;
    let login = CheckMenuItem::with_id(
        app,
        "login",
        "Start at login",
        true,
        app.autolaunch().is_enabled().unwrap_or(false),
        None::<&str>,
    )?;
    let quit = MenuItem::with_id(app, "quit", "Quit Alpha Harness", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&open, &login, &separator, &quit])?;

    let toggled = login.clone();
    let mut builder = TrayIconBuilder::with_id("main")
        .tooltip(format!("Alpha Harness {}", supervisor::BUILD_VERSION))
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "open" => show(app),
            "login" => {
                let autostart = app.autolaunch();
                let on = autostart.is_enabled().unwrap_or(false);
                let done = if on {
                    autostart.disable()
                } else {
                    autostart.enable()
                };
                // Said as it now is, whether or not the change took.
                let _ = toggled.set_checked(autostart.is_enabled().unwrap_or(on));
                if let Err(error) = done {
                    supervisor::note(&format!("could not change start at login: {error}"));
                }
            }
            "quit" => supervisor::quit(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

fn main() {
    let app = tauri::Builder::default()
        // First, as the plugin asks: a second launch only brings this one's window forward.
        .plugin(tauri_plugin_single_instance::init(|app, _, _| show(app)))
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(Status::default())
        .invoke_handler(tauri::generate_handler![startup_status])
        .setup(|app| {
            tray(app.handle())?;
            supervisor::spawn(app.handle().clone());
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window keeps Alpha Harness in the tray, so tasks keep simulating.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("Alpha Harness could not open its window");

    app.run(|app, event| match event {
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => show(app),
        // Asked by the system (⌘Q, the Dock's Quit, logging out) rather than by this program, whose
        // own exit names a code: held until the backend has closed and let go of DuckDB.
        RunEvent::ExitRequested {
            api, code: None, ..
        } => {
            api.prevent_exit();
            supervisor::quit(app);
        }
        _ => {
            let _ = app;
        }
    });
}
