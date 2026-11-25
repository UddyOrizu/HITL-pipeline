# On hold

This page lists work that needs a person to decide before it can go out, and work that was sent back to where it came from. Nothing on this page has been released.

**Who can use this:** People with the oversight lead role. Admins have this too.

**Where to find it:** In the menu on the left, under **Quality**, select **On hold**.

## How to find an item

1. Choose a tab:
   - **Held** – work that is waiting for you or for a reviewer.
   - **Deferred** – work that was sent back to where it came from, or that could not be processed.
2. To narrow the list, type in the **Search by title, reference or id** box. The list updates when you stop typing. The number of items found is shown next to the box.
3. If there are more than 25 items, use **Previous** and **Next** at the bottom of the list.
4. Select **Refresh** at the top right to load the latest list.

### What the columns mean

| Column | What it shows |
| --- | --- |
| **Item** | The title (or reference) of the work. Underneath: its workflow, the connected app it came from, and its status. |
| **Why it is held** (on the **Held** tab) | The reason, with a one-line explanation. An **escalated** label appears if the item has been escalated. |
| **Why it was deferred** (on the **Deferred** tab) | The reason it was deferred, and whether it was deferred "by an oversight lead" or "by the system". If processing failed, it shows "Processing failed:" and the error. |
| **Risk** | The risk level of the work. |
| **Score** | The review quality score, or **–** if there is none yet. |
| **Received** / **Deferred** | When the work arrived (**Held** tab) or when it last changed (**Deferred** tab). |
| **Can do** | The actions available for this item. |

## Why work is on hold

The **Why it is held** column shows one of these reasons.

| Reason shown | What the page tells you |
| --- | --- |
| **spot check disagreement** | The blind second reviewer disagreed with the first. Decide the outcome. |
| **below threshold** | The review quality score was below this workflow's minimum, so the work can't be released automatically. |
| **awaiting capacity** | Every eligible reviewer is at their review budget. It will be assigned when capacity frees up, or assign it now. |
| **awaiting spot check reviewer** | No eligible second reviewer is available for the spot check. Assign one, or decide the outcome yourself. |
| **release check failed** | The content about to be released did not match the original submission. Investigate before releasing. |

## How to open an item and see the detail

1. Select the item's row. A panel opens on the right.
2. Read the labels at the top: status, reason for the hold, **escalated** (if it applies) and the risk level. The explanation of the hold reason is underneath.
3. Check the summary: **Workflow**, **Source**, **Received**, **Last change**, **Final outcome** (if there is one) and **Evidence pack**.
4. Scroll down for the sections described below.
5. To close the panel, select **✕**, press Esc, or click outside the panel.

| Section | What it shows |
| --- | --- |
| **Actions** | The buttons for what you can do with this item. See the tasks below. |
| **Decisions** | One card for each review that has a decision: who decided, the outcome, when, their reason, and their reading activity (**Active time**, **Reading ratio**, **Section coverage**, and, where they apply, **Flags acknowledged**, **Focus lost** and **Comments / edits**). Any comments and suggested edits are listed too. Reviews with no decision appear under **Open or closed without a decision**. |
| **Review quality score** | The score out of 100, a **below workflow minimum** label if it applies, and a table showing how the score is made up (**Component**, **Value**, **Weight**, **Points**). |
| **Risk flags** | Each risk found in the document, most serious first, with the reason and the text it refers to. **must acknowledge** marks flags a reviewer has to acknowledge. |
| **Original document** | The text of the work. Passages with a risk flag are highlighted. |

If a decision card says "No reading activity recorded: the decision was made outside this app.", the reviewer decided through a connected tool rather than on screen, so there is nothing to measure.

A card marked **SLA breached** means that review missed its deadline.

## How to decide the outcome yourself

Use this when the item offers **Decide outcome**, for example after a spot check disagreement.

1. Open the item and, under **Actions**, select **Decide outcome**.
2. In **Outcome**, choose **Approve**, **Request changes** or **Reject**.
3. Type a **Reason**. It is optional for **Approve** and required for **Request changes** and **Reject**. The reason is recorded in the review record.
4. Select **Record decision**.
5. If you chose **Reject**, the page asks: "Rejecting releases a final rejection to the connected app. Continue?" Select **Yes, confirm** to go ahead, or **Go back** to change your mind.
6. A green message confirms it, for example "Decision recorded: approve."

## How to assign a reviewer

Use this when the item offers **Assign reviewer**, for example when it is waiting for a free reviewer.

1. Open the item and, under **Actions**, select **Assign reviewer**.
2. In **Reviewer**, choose a person from the list.
3. Select **Assign reviewer**.
4. A green message confirms who it was assigned to.

Assigning this way goes past the workload caps. People who already reviewed the item, or wrote it, can't be chosen, so they do not appear in the list.

## How to send an item back to where it came from (defer)

Use this when the item offers **Defer to source**.

1. Open the item and, under **Actions**, select **Defer to source**.
2. Type a reason in **Reason (sent to the connected app)**. This is required.
3. Select **Defer to source**.
4. The page asks: "Deferring stops review and notifies the connected app. Continue?" Select **Yes, confirm**, or **Go back**.
5. A green message confirms: "Deferred. The connected app has been told."

Open review tasks close and the item stops here. It moves to the **Deferred** tab and can be retried later.

## How to retry an item

Use this when the item offers **Retry**, for example on the **Deferred** tab.

1. Open the item and, under **Actions**, select **Retry**.
2. Read the note: the item goes back to processing if that failed, otherwise back into review.
3. Select **Retry**. There is no extra confirmation.
4. A green message confirms: "Sent back for processing or review."

## How to cancel an item

Use this when the item offers **Cancel item**.

1. Open the item and, under **Actions**, select **Cancel item**.
2. Read the note: open review tasks close and the connected app is told it was cancelled.
3. Select **Cancel item**.
4. The page asks: "Cancelling can't be undone. Are you sure?" Select **Yes, confirm**, or **Go back**.
5. A green message confirms: "Cancelled."

## How to download the evidence for an item

1. Open the item.
2. Next to **Evidence pack**, select **Download evidence PDF**.

If it says "Not generated yet", there is no evidence pack for this item so far. Once one exists you can also get it from [Review records](review-records.md).

## Common questions

**The action I want isn't there.**
Each item only offers the actions that make sense for its current state. The **Can do** column shows them before you open the item.

**It says "No actions are available for this item in its current state."**
There is nothing you can do with this item at the moment.

**The button I need is greyed out.**
Something required is missing: a reason (for **Request changes**, **Reject** or **Defer to source**) or a reviewer (for **Assign reviewer**).

**The list says "Nothing is held." or "Nothing has been deferred."**
There is no work in that tab. "No items match." means your search found nothing; clear the search box.

**I see "Processing error:" in red.**
The app could not process this item. The message after it gives the cause. You can try **Retry** if it is offered.

**The document says "The content has been purged under the retention policy. The record and hashes remain."**
The text has been removed after the time your organisation keeps it for. The record of what happened is still kept. See [Organisation settings](settings-organisation.md).

**Where do I see how much work is on hold overall?**
See [Quality overview](quality-overview.md) and the **Work on hold, by reason** chart in [Reports](reports.md).
