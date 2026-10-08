package io.github.guillaumeboileaupro.controltv

/**
 * The widget-tap boundary, between a button's pending intent and the background job, kept
 * free of Android types so it can be tested.
 *
 * Every drawing of the widget gives its buttons one token ([WidgetStore.armedToken]),
 * carried by explicit, immutable pending intents. A tap consumes the token durably before
 * anything is queued; the same interaction delivered again carries the same, already consumed
 * token and is ignored. The token becomes the tap's identity in the job, so a job run again
 * by WorkManager is caught by [TapGuard]. Only the action name (a closed set) and the token
 * come from the intent: never a device, a level or any other value.
 *
 * Nothing here is silent: a rejected token redraws the widget (so buttons left with an old
 * token by the launcher are armed again; the rejected tap itself still does nothing), a tap
 * with no TV chosen says so in words that differ from the widget's first drawing, and every
 * step is logged through [log] without any token, device or network value.
 */
class WidgetTapHandler(
    private val store: WidgetStore,
    private val now: () -> Long,
    private val newToken: () -> String,
    private val enqueue: (TapSpec) -> Unit,
    private val render: () -> Unit,
    private val log: (String) -> Unit = {},
) {
    /** Returns true when the tap was accepted (it may still end without any command). */
    fun handle(actionName: String?, token: String?): Boolean {
        val action = WidgetAction.entries.firstOrNull { it.name == actionName }
        if (action == null) {
            log("widget tap ignored: unknown action")
            return false
        }
        log("widget tap received: ${action.name}")
        if (token.isNullOrBlank() || !store.consumeToken(token, newToken())) {
            log("widget tap rejected: stale or repeated (${action.name}); widget redrawn")
            render()
            return false
        }
        log("widget tap accepted: ${action.name}")
        val selection = store.selection()
        if (selection == null) {
            log("widget tap: no TV chosen in the app, nothing queued")
            store.saveView(WidgetPresentation.noSelectionAfterTap())
            render()
            return true
        }
        store.saveView(WidgetPresentation.busy(store.view(), selection))
        render()
        enqueue(TapSpec(action, token, now()))
        log("widget job enqueued: ${action.name}")
        return true
    }
}
