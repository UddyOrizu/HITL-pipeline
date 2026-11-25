# Activity log

This page lists everything that has happened in your organisation, newest first. Entries are linked together, so any change to the record would be detected.

**Who can use this:** People with the oversight lead or auditor role. Admins have this too.

**Where to find it:** In the menu on the left, under **Records**, select **Activity log**.

## How to check the log has not been tampered with

The page checks the whole log each time it opens. While it does, you see "Verifying the audit chain…".

1. Look at the banner near the top of the page.
2. A green banner starting **Audit chain verified.** means every entry still matches the fingerprint stored for it. The banner gives the number of events checked.
3. A red banner starting **Audit chain broken at event #…** means the numbered entry, or one before it, was changed or removed after it was written. That entry is also marked **broken** in the list. Tell your administrator straight away.
4. To run the check again and reload the list, select **Refresh** at the top right.

The line starting **Head** under the banner is the fingerprint of the most recent entry. You do not need to read it; it can be noted down as proof of the state of the log at that moment.

## How to filter the log

The filters are in a panel above the list. You can use several together.

| Filter | What to enter |
| --- | --- |
| **Action** | Choose one kind of event from the list. The group **Every action of a kind** at the top lets you pick a whole family at once, for example everything starting with "user.". The number in brackets is how many kinds of action are in that family. |
| **Entity** | The type of thing the event was about, for example "submission". Start typing to see suggestions. |
| **Entity id** | The exact id of one thing, to see only its events. |
| **Actor** | Who or what did it. People appear as "user:…" and automatic steps as, for example, "system:workflow". |
| **From** | The first day to include. |
| **To (inclusive)** | The last day to include. The whole of that day is included. |

1. Set the filters you need. Lists and dates apply straight away. Typed filters apply when you stop typing.
2. Under the filters, the page shows how many events match and how many are showing, for example "120 events match · showing 50".
3. To remove all filters, select **Clear filters**.

### A quick way to follow one item

1. Find any entry about the item.
2. In the **Entity** column, select the small button showing the id under the entity name.
3. The **Entity** and **Entity id** filters are filled in for you, and the list shows only that item's events.

## How to read an entry

Each row is one event.

| Column | What it shows |
| --- | --- |
| **#** | The entry's number in the log. Numbers run in order with no gaps. |
| **When** | The date and time. Hold your mouse pointer over it to see the full, exact time. |
| **Action** | What happened, written as a short code such as "user.created". |
| **Entity** | The type of thing it happened to, with a button showing its id. |
| **Actor** | Who or what did it. |
| **Details** | A short preview of the extra information recorded, or "No payload" if there is none. |

To see an entry in full:

1. Select the row. It opens underneath.
2. The top box shows all the extra information recorded for the event.
3. The lines below show the exact time, the full id, and two fingerprints: **prev** (the fingerprint of the entry before) and **hash** (this entry's own fingerprint). These are what link the entries together.
4. Select the row again to close it. You can have several entries open at once.

## How to see older entries

The page loads 50 entries at a time.

1. Scroll to the bottom of the list.
2. Select **Load older (… more)**. The number tells you how many entries are still to load.
3. The next 50 are added to the bottom. Repeat as needed.

## How to export entries

1. Set your filters.
2. If you need more than the first 50 entries, select **Load older** until everything you want is on screen.
3. Select **Export loaded events (JSON)** at the top right.
4. A file is downloaded, named with today's date.

The file contains only the entries loaded on screen, not everything that matches. It also records when it was exported, the filters you used, the total number that matched, the number exported, and the result of the tamper check. The file is in a format meant for other software rather than for reading; pass it to whoever asked for it.

## Common questions

**"No events match these filters."**
Nothing fits. Loosen a filter or select **Clear filters**. Note that **Entity id** must be the exact id.

**"No events yet."**
Nothing has been recorded so far.

**The Export button is greyed out.**
There are no entries on screen to export.

**The Action list only shows "All actions".**
The list of actions could not be loaded. Select **Refresh**, or filter by **Entity**, **Actor** or dates instead.

**Can I edit or delete an entry?**
No. The page only lets you read and export.

**Where do I see the full story of one piece of work?**
Open it in [Review records](review-records.md), which shows its decision, reviews and audit trail together. For the terms used here, see the [glossary](glossary.md).
