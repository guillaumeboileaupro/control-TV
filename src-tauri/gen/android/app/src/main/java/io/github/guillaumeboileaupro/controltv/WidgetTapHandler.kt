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
 */
class WidgetTapHandler(
    private val store: WidgetStore,
    private val now: () -> Long,
    private val newToken: () -> String,
    private val enqueue: (TapSpec) -> Unit,
    private val render: () -> Unit,
) {
    /** Returns true when the tap was accepted (it may still end without any command). */
    fun handle(actionName: String?, token: String?): Boolean {
        val action = WidgetAction.entries.firstOrNull { it.name == actionName } ?: return false
        if (token.isNullOrBlank()) return false
        if (!store.consumeToken(token, newToken())) return false
        val selection = store.selection()
        if (selection == null) {
            store.saveView(WidgetPresentation.noSelection())
            render()
            return true
        }
        store.saveView(WidgetPresentation.busy(store.view(), selection))
        render()
        enqueue(TapSpec(action, token, now()))
        return true
    }
}
