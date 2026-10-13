//! The status-area icon (tray) of the desktop application: a second view over the window.
//!
//! The tray owns no state and no control logic. "Open" and "Quit" act on the application; every
//! TV action is handed to the window as a `tray-action` event, and the window runs it exactly
//! like its own button (the same selected TV, the same single request in flight, the same
//! wording of what the TV confirmed or not). Closing the window quits the application, as
//! before: the tray is a quick-control companion while the application runs, not a background
//! service.

/// The TV actions the tray offers, by the identifier the window handles, and their label.
pub(crate) const ACTIONS: [(&str, &str); 5] = [
    ("play_pause", "Play / Pause"),
    ("stop", "Stop"),
    ("tv_mute", "Mute the TV"),
    ("tv_volume_down", "TV volume −"),
    ("tv_volume_up", "TV volume +"),
];
pub(crate) const OPEN: &str = "open";
pub(crate) const QUIT: &str = "quit";
/// The event the window listens to; its payload is one identifier of `ACTIONS`.
pub(crate) const EVENT: &str = "tray-action";

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum TrayEvent {
    Open,
    Quit,
    Action(&'static str),
    Unrecognized,
}

/// What a click on a tray menu item means. Pure, so it is tested without a desktop.
pub(crate) fn event_for(id: &str) -> TrayEvent {
    match id {
        OPEN => TrayEvent::Open,
        QUIT => TrayEvent::Quit,
        _ => ACTIONS
            .iter()
            .find(|(action, _)| *action == id)
            .map_or(TrayEvent::Unrecognized, |(action, _)| {
                TrayEvent::Action(action)
            }),
    }
}

#[cfg(desktop)]
pub(crate) fn install(app: &tauri::App) -> tauri::Result<()> {
    use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
    use tauri::tray::TrayIconBuilder;

    let open = MenuItem::with_id(app, OPEN, "Open control-TV", true, None::<&str>)?;
    let mut items: Vec<Box<dyn tauri::menu::IsMenuItem<tauri::Wry>>> = vec![
        Box::new(open),
        Box::new(PredefinedMenuItem::separator(app)?),
    ];
    for (id, label) in ACTIONS {
        items.push(Box::new(MenuItem::with_id(
            app,
            id,
            label,
            true,
            None::<&str>,
        )?));
    }
    items.push(Box::new(PredefinedMenuItem::separator(app)?));
    items.push(Box::new(MenuItem::with_id(
        app,
        QUIT,
        "Quit control-TV",
        true,
        None::<&str>,
    )?));
    let refs: Vec<&dyn tauri::menu::IsMenuItem<tauri::Wry>> =
        items.iter().map(|item| item.as_ref()).collect();
    let menu = Menu::with_items(app, &refs)?;
    let mut builder = TrayIconBuilder::with_id("control-tv")
        .tooltip("control-TV")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| handle(app, event.id().as_ref()));
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

#[cfg(desktop)]
fn handle(app: &tauri::AppHandle, id: &str) {
    use tauri::{Emitter, Manager};

    match event_for(id) {
        TrayEvent::Open => show_main_window(app),
        // Ending the application closes the bridge's stdin, which ends the bridge too.
        TrayEvent::Quit => app.exit(0),
        TrayEvent::Action(action) => {
            if app.get_webview_window("main").is_some() {
                let _ = app.emit_to("main", EVENT, action);
            }
        }
        TrayEvent::Unrecognized => {}
    }
}

#[cfg(desktop)]
pub(crate) fn show_main_window(app: &tauri::AppHandle) {
    use tauri::Manager;

    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}
