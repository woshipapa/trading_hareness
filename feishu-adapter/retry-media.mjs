/**
 * A Feishu-backed retry must re-read its immutable source message. This avoids
 * retrying a stale or partially written local copy and keeps the retry boundary
 * at the source message ID. A manual relay has no source resource to download.
 */
export function shouldRedownloadRetryMedia({ expectedResourceCount = 0, event = null, localResourcesAvailable = false }) {
	// A completed local asset is immutable and can resume from its durable part
	// manifest. Re-download only when the original Feishu event is available and
	// the local copy was removed/corrupted; this avoids repeatedly pulling large
	// media files when the remote workflow is the component that is failing.
	return Boolean(event?.message?.message_id && expectedResourceCount > 0 && !localResourcesAvailable);
}
