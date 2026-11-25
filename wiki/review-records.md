# Review records

This page lists every piece of work sent for review, with the proof of how it was reviewed. You can read the records and download them, but you cannot change them.

**Who can use this:** People with the oversight lead or auditor role. Admins have this too.

**Where to find it:** In the menu on the left, under **Records**, select **Review records**.

## How to check the records can be trusted

Every action in the app is written to a chain of linked entries, so that any later change would show up. The page checks this chain each time it opens.

1. Look at the label at the top right of the page.
2. A green **Audit chain verified** label, followed by the number of events, means the record is intact.
3. A red **Audit chain broken at event #…** label means something is wrong. A red message below explains that the numbered event, or one before it, was changed or removed after it was written, and that records after it can't be relied on until this is investigated. Tell your administrator straight away.

You can look at the entries themselves in the [Activity log](activity-log.md).

## How to find a record

1. Type in the **Search** box. You can search by title, reference or submission id. The list updates when you stop typing.
2. To narrow by stage, choose a **Status**: **received**, **processing**, **in review**, **spot check**, **approved**, **rejected**, **changes requested**, **deferred** or **cancelled**. Choose **Any status** to see everything.
3. The number of submissions found is shown next to the filters.
4. If there are more than 25, use **Previous** and **Next** at the bottom of the list.

### What the columns mean

| Column | What it shows |
| --- | --- |
| **Item** | The title of the work (or its reference). Underneath: the start of its id, and its workflow. |
| **Status** | Where the work has got to. A **content purged** label means the document text has been removed (see below). |
| **Score** | The review quality score, or **–** if there is none. A **below** label means it was under the minimum. |
| **Origin** | Where the content came from, as recorded when it was sent. |
| **Released** | When the final decision was released, or **–** if it has not been. |
| (last column) | A **PDF** button, when a downloadable record exists. |

## How to open a record

1. Select the row. The record opens underneath it.
2. Select the row again to close it. Only one record is open at a time.

### What the record shows

**Decision**

| Line | What it means |
| --- | --- |
| **Outcome** | The final outcome, or the current status if there is no final outcome yet. |
| **Review quality score** | The score. "(below the minimum)" appears if it was under the workflow's minimum. |
| **Workflow** | The workflow used, and its version number. |
| **Planted mistake** | "none" if no planted mistake was added. Otherwise, for each one: its difficulty, its type, and whether it was "spotted" or "missed". |
| **Spot check** | "not sampled" if the work was not picked for a spot check; "pending" if the spot check is not finished; otherwise "agreed" or "disagreed", with the second reviewer's outcome. |
| **Audit chain** | "valid", with the number of events, or "BROKEN". |

**Content hashes**

A long code for each document in the record. The code is a fingerprint of the content: if the content changed, the code would be different. You do not need to read it; it is there so the record can be checked later.

**Reviews**

One entry for each review: the workflow stage, the kind of review, and the outcome (or the review's status if it has no decision). A label shows where the decision was made: **via workspace** means on screen in this app; anything else is the connected tool that was used. Where reading activity was recorded, a line shows the active time against the expected time, in seconds, and the coverage (the share of the content viewed).

**Audit trail**

A list of everything that happened to this piece of work, in order. Each line has a number, what happened, who or what did it, and the start of that entry's fingerprint. Scroll within the list to see it all. To see an entry in full, look it up in the [Activity log](activity-log.md).

## How to download a record

1. Find the item in the list.
2. Select **PDF** at the end of its row.
3. A PDF file of the record is downloaded.

If there is no **PDF** button, no downloadable record has been produced for that item yet.

## Common questions

**"No submissions match."**
Nothing fits your search and status. Clear the **Search** box or set **Status** to **Any status**.

**"No submissions."**
Nothing has been sent for review yet.

**"The document content was purged under the retention policy. Hashes, decisions and the audit trail remain."**
The document text has been removed after the time your organisation keeps it for. The decisions, fingerprints and audit trail are still there. See [Organisation settings](settings-organisation.md).

**Can I correct a record?**
No. Records can be read but not changed.

**A red error message appears after I select PDF.**
The download failed. The message gives the reason. Try again, and tell your administrator if it keeps happening.

**Where do I act on work that has not been released?**
See [On hold](on-hold.md). For the terms used here, see the [glossary](glossary.md).
