# Reports

This page shows how much work was reviewed, how long it took, what the outcomes were and whether deadlines were met. Oversight leads also see how each reviewer is doing and which tasks are overdue.

**Who can use this:** People with the oversight lead or auditor role. Admins have this too. The **Reviewers** and **SLA** tabs are only shown to people with oversight lead access; everyone else sees the overview only, with no tabs.

**Where to find it:** In the menu on the left, under **Reports**, select **Reports**.

All dates on this page are in UTC, which may differ from your local time by a few hours.

## How to choose the dates and filters

The filters sit in a bar near the top of the **Overview** and **Reviewers** tabs.

1. Under **Range**, select **7 days**, **30 days** or **90 days**. The page starts on 30 days.
2. Or set your own dates in **From** and **To**. **To** cannot be later than today, and the range can be at most two years.
3. On the **Overview** tab, choose an **Interval**: **Daily**, **Weekly** or **Monthly**. This sets how the charts group the dates.
4. To look at one workflow, choose it in **Workflow**. To see everything, leave it on **All workflows**.
5. To look at work from one connected app, choose it in **Connected app**. To see everything, leave it on **All connected apps**.

The page reloads each time you change something.

Your choices are kept in the page's web address. You can bookmark the address, or copy it to a colleague who has access, to open the same report again.

## How to read the Overview tab

### Headline figures

| Figure | What it means | Detail underneath |
| --- | --- | --- |
| **Received** | Items that arrived in the date range. | How many are in progress, deferred and cancelled. |
| **Decided** | Items released with a final decision in the range. | How many were approved, rejected, or had changes requested. |
| **Approval rate** | Share of decided items approved. | – |
| **Median turnaround** | The middle time from received to released. | The 90th percentile: nine out of ten items were quicker than this. |
| **SLA breach rate** | The share of review tasks due in the range that missed their deadline. | How many missed, out of how many were due. |
| **Escalated** | Items received in the range that were escalated. | – |
| **Held now** | Items on hold at this moment, whenever they were received. | – |
| **Typical review quality score** | The middle review quality score. | How many items were decided below the minimum score. |

### Charts

| Chart | What it shows |
| --- | --- |
| **Received and decided per day** (or week, or month) | Two sets of bars: items received, and items released with a final decision. |
| **Median turnaround per day** (or week, or month) | A line showing the hours from received to released, for items decided in each period. |
| **Decisions by channel** | Where reviewers recorded their decisions. **Web app** means on screen in this app. **API** and **MCP** mean the decision came through a connected tool. |
| **Work on hold, by reason** | Items on hold right now, by reason. This follows the **Workflow** and **Connected app** filters but not the dates. To deal with these items, go to [On hold](on-hold.md). |

### How to see a chart's exact numbers

1. Select **Show table** at the top right of the chart.
2. The chart is replaced by a table of the same numbers. The table for **Received and decided** also splits decisions into **Approved**, **Rejected** and **Changes requested**.
3. Select **Show chart** to go back.

### How to use the Breakdown table

1. Find the **Breakdown** panel below the first two charts.
2. Choose how to split the figures: **Workflow**, **Connected app**, **Risk tier** or **Content origin**.
3. Read the columns: **Received**, **Decided**, **Approved**, **Approval rate**, **Median turnaround** and **Median score**.
4. To sort, select a column heading. Select it again to reverse the order. A small arrow (▲ or ▼) shows which column is sorted and in which direction.

**Received** counts items received in the range. **Decided**, **Approval rate** and **Median turnaround** count items released in the range, so the two will not always add up.

A row labelled **Not set** groups items that have no value for the thing you split by.

## How to check reviewer performance

*Oversight leads only.*

1. Select the **Reviewers** tab.
2. Set the date range. Only the dates apply here: the report covers all workflows and connected apps, and the page tells you so if you have one of those filters set.
3. Read the **Reviewer performance** table. It lists active reviewers, plus anyone who recorded a decision in the range. An **inactive** label marks people who are no longer active.
4. To sort, select a column heading; select it again to reverse. The table starts sorted by **Tasks**, highest first.
5. Hold your mouse pointer over a column heading to see a short explanation, where one is available.

| Column | What it means |
| --- | --- |
| **Tasks** | Decisions recorded in the range. |
| **Primary** | How many of those were first reviews. |
| **Spot checks** | How many were spot checks. |
| **Approval rate** | Share of their decisions that were approvals. |
| **Median active time** | Measured reading time per decision in the web app, in minutes. |
| **Reading ratio** | Middle share of the expected reading time actually spent. |
| **Coverage** | Average share of the content viewed. |
| **Flag engagement** | Average share of risk flags looked at. |
| **Warnings confirmed** | How many warnings the reviewer confirmed when deciding. |
| **Planted mistakes** | Planted mistakes checked in this period. |
| **Mistakes caught** | Share of planted mistakes spotted. |
| **Spot check disagreements** | Spot checks that reached a different outcome. |
| **SLA breaches** | Deadlines missed. |
| **Open** | Tasks assigned to them now. |
| **Overdue** | Tasks assigned now and past due. Shown in red when above zero. |
| **Channels** | How many decisions they made in each place (**Web app**, **API**, **MCP**). |

The reading measures (**Median active time**, **Reading ratio**, **Coverage**, **Flag engagement**) only apply to decisions made in this app. A dash (**–**) means there is nothing to measure.

## How to see overdue tasks

*Oversight leads only.*

1. Select the **SLA** tab. SLA means the deadline a review is expected to meet.
2. The **Overdue tasks** table lists assigned tasks that are past their due time right now. The most overdue is at the top.
3. Select **Refresh** to update the list.

This tab has no filters and is not affected by the date range.

| Column | What it shows |
| --- | --- |
| **Item** | The title of the work (or a short id if it has no title). |
| **Stage** | The stage of the workflow the task belongs to. |
| **Kind** | Whether it is a first review or a spot check. |
| **Reviewer** | Who it is assigned to, or **Unassigned**. |
| **Due** | When it was due. |
| **Overdue by** | How late it is. |
| **Escalated** | **Escalated** if it has been escalated, otherwise **No**. |

## How to export a report

1. Open the tab you want and set the dates and filters.
2. Select **Export CSV** at the top right. The button shows **Exporting…** while it works.
3. A file is downloaded that you can open in a spreadsheet program.

What you get depends on the tab:

- **Overview** – the overview for your dates, interval and filters.
- **Reviewers** – the reviewer report for your dates.
- **SLA** – the tasks overdue right now.

## Common questions

**"Choose a start and end date."**
One of the date boxes is empty. Fill in both **From** and **To**.

**"The start date must be on or before the end date."**
Swap or correct the dates.

**"The range can be at most two years."**
Shorten the range.

**"Nothing was received or decided in this range. Try a longer range or clear the filters."**
There is no activity for your dates and filters. Widen the dates, or set **Workflow** and **Connected app** back to "All".

**A chart says "No decisions in this range." or "Nothing is held."**
There is nothing to draw for that chart. The rest of the page still works.

**Export CSV is greyed out.**
On **Overview** and **Reviewers**, the dates are not valid. On **SLA**, nothing is overdue, so there is nothing to export.

**I can't see the Reviewers or SLA tabs.**
They are only shown to people with oversight lead access.

**Where are the costs?**
See [Costs](costs.md). For the proof behind a single decision, see [Review records](review-records.md).
