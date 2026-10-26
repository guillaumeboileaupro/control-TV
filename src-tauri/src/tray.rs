//! The GNOME status-area menu: a compact second view over the Ubuntu window.
//!
//! It owns no TV or streaming logic. The frontend publishes only display state (selected TV,
//! confirmed connection and streaming), and every click is emitted back to the window so the
//! existing commands and consent checks remain authoritative.

/// Actions forwarded to the window, in menu order. Open and Quit are handled by Tauri itself.
pub(crate) const ACTIONS: [(&str, &str); 8] = [
    ("select_tv", "Sélectionner / connecter une TV"),
    ("play_pause", "Lecture / Pause"),
    ("stop", "Arrêter"),
    ("tv_mute", "Muet"),
    ("tv_volume_down", "Volume −"),
    ("tv_volume_up", "Volume +"),
    ("duplicate_screen", "Dupliquer l’écran"),
    ("stop_stream", "Arrêter la diffusion"),
];

pub(crate) const OPEN: &str = "open";
pub(crate) const QUIT: &str = "quit";
pub(crate) const EVENT: &str = "tray-action";
const CONNECTION_STATUS: &str = "connection_status";
const STREAM_STATUS: &str = "stream_status";

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct TrayState {
    pub(crate) selected_name: Option<String>,
    pub(crate) connected: bool,
    pub(crate) streaming: bool,
}

impl TrayState {
    pub(crate) fn connection_label(&self) -> String {
        match (&self.selected_name, self.connected) {
            (Some(name), true) => format!("TV : {name} — connectée"),
            (Some(name), false) => format!("TV : {name} — non connectée"),
            (None, _) => "TV : aucune sélection".to_string(),
        }
    }

    pub(crate) fn stream_label(&self) -> &'static str {
        if self.streaming {
            "Diffusion : active"
        } else {
            "Diffusion : arrêtée"
        }
    }

    pub(crate) fn enabled(&self, id: &str) -> bool {
        match id {
            "select_tv" => true,
            "stop_stream" => self.streaming,
            "duplicate_screen" => self.connected && !self.streaming,
            _ => self.connected,
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum TrayEvent {
    Open,
    Quit,
    Action(&'static str),
    Unrecognized,
}

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
fn menu(app: &tauri::AppHandle, state: &TrayState) -> tauri::Result<tauri::menu::Menu<tauri::Wry>> {
    use tauri::menu::{IsMenuItem, Menu, MenuItem, PredefinedMenuItem};

    let open = MenuItem::with_id(app, OPEN, "Ouvrir ControlTV", true, None::<&str>)?;
    let connection = MenuItem::with_id(
        app,
        CONNECTION_STATUS,
        state.connection_label(),
        false,
        None::<&str>,
    )?;
    let stream = MenuItem::with_id(
        app,
        STREAM_STATUS,
        state.stream_label(),
        false,
        None::<&str>,
    )?;
    let mut items: Vec<Box<dyn IsMenuItem<tauri::Wry>>> = vec![
        Box::new(open),
        Box::new(connection),
        Box::new(stream),
        Box::new(PredefinedMenuItem::separator(app)?),
    ];
    for (id, item_label) in ACTIONS {
        items.push(Box::new(MenuItem::with_id(
            app,
            id,
            item_label,
            state.enabled(id),
            None::<&str>,
        )?));
    }
    items.push(Box::new(PredefinedMenuItem::separator(app)?));
    items.push(Box::new(MenuItem::with_id(
        app,
        QUIT,
        "Quitter",
        true,
        None::<&str>,
    )?));
    let refs: Vec<&dyn IsMenuItem<tauri::Wry>> = items.iter().map(|item| item.as_ref()).collect();
    Menu::with_items(app, &refs)
}

#[cfg(desktop)]
pub(crate) fn install(app: &tauri::App) -> tauri::Result<()> {
    use tauri::tray::TrayIconBuilder;

    let initial_menu = menu(app.handle(), &TrayState::default())?;
    let mut builder = TrayIconBuilder::with_id("control-tv")
        .tooltip("ControlTV")
        .menu(&initial_menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| handle(app, event.id().as_ref()));
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

#[cfg(desktop)]
pub(crate) fn update(app: &tauri::AppHandle, state: TrayState) -> Result<(), String> {
    let tray = app
        .tray_by_id("control-tv")
        .ok_or_else(|| "the ControlTV status icon is unavailable".to_string())?;
    let updated = menu(app, &state).map_err(|error| error.to_string())?;
    tray.set_menu(Some(updated))
        .map_err(|error| error.to_string())
}

#[cfg(desktop)]
fn handle(app: &tauri::AppHandle, id: &str) {
    use tauri::{Emitter, Manager};

    match event_for(id) {
        TrayEvent::Open => show_main_window(app),
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
