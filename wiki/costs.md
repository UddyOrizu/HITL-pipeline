# Costs

This page shows what reviewing has cost: the spend on the AI models the app uses (**Model spend**) and the cost of reviewers' time. You can split the cost by connected app, workflow, team, reviewer and more.

**Who can use this:** People with the oversight lead role. Admins have this too.

**Where to find it:** In the menu on the left, under **Reports**, select **Costs**.

All dates on this page are in UTC, which may differ from your local time by a few hours.

## How to choose the dates and filters

1. Under **Range**, select **7 days**, **30 days** or **90 days**. The page starts on 30 days.
2. Or set your own dates in **From** and **To**. **To** cannot be later than today, and the range can be at most two years.
3. To look at one workflow, choose it in **Workflow**. To see everything, leave it on **All workflows**.
4. To look at one connected app, choose it in **Connected app**. To see everything, leave it on **All connected apps**.

Your choices are kept in the page's web address, so you can bookmark it or send it to a colleague who has access.

## How to split the costs a different way

1. Find **Group by** in the filter bar. It starts on **Connected app**.
2. Choose one of: **Connected app**, **Workflow**, **Team**, **Reviewer**, **Model**, **Submission**, **Day**, **Week** or **Month**.
3. The chart and the **Breakdown** table change to match. The line under the page title also tells you the current grouping.

Two things to know:

- When you group by **Team** or **Reviewer**, model spend is not a reviewer cost, so it appears as its own "Model usage" row.
- When you group by **Model**, reviewer time appears as its own "Reviewer time" row.

## How to read the headline figures

| Figure | What it means | Detail underneath |
| --- | --- | --- |
| **Total cost** | Model spend plus reviewer time, in your organisation's currency. | – |
| **Model spend** | The cost of the AI models used. | The number of model calls, or, if your currency is not US dollars, the amount in US dollars and the exchange rate used. |
| **Reviewer time cost** | Reviewers' time multiplied by their hourly rate. | The number of completed review tasks. |
| **Cost per submission** | The total divided by the number of submissions that had costs. | The number of submissions with costs. |
| **Tokens** | The amount of text the models processed. Tokens are the units model prices are based on. | How many went in and how many came out. |
| **Review hours** | Total reviewer time in hours. | How much was measured and how much was estimated. |

## How to read the chart

The chart shows two colours: **Model spend** and **Reviewer time**.

- If you group by **Day**, **Week** or **Month**, the chart is called **Cost per day** (or week, or month) and shows one stacked column per period. Periods with no cost are still shown, so the timeline has no gaps.
- For any other grouping, the chart is called, for example, **Cost by connected app**, and shows one bar per group, largest first. If there are more than 12 groups, it shows the 11 largest and adds the rest together as **Other**. The table underneath still lists every group.

To see the exact numbers:

1. Select **Show table** at the top right of the chart.
2. Select **Show chart** to go back.

## How to use the Breakdown table

1. Scroll to the **Breakdown** panel. It lists every group, in your currency.
2. To sort, select a column heading. Select it again to reverse the order. A small arrow (▲ or ▼) shows the sorted column.
3. The **Total** row at the bottom adds up all groups.

The table starts sorted by **Total**, highest first. When grouped by day, week or month it starts in date order instead.

| Column | What it means |
| --- | --- |
| **Total** | Model spend plus reviewer time cost. |
| **Model spend** | Cost of AI model use. |
| **Model spend (USD)** | The same in US dollars. Only shown when your currency is not US dollars. |
| **Reviewer time cost** | Cost of reviewers' time. |
| **Input tokens** | Text sent to the models. Includes cache reads and writes. |
| **Output tokens** | Text the models produced. |
| **Model calls** | How many times a model was used. |
| **Measured time** | Active reading time measured in the workspace. |
| **Estimated time** | An estimate used where no reading activity was recorded, because the decision was made through a connected tool rather than on screen. |
| **Unrated time** | Reviewer time with no hourly rate. It costs nothing here. |
| **Tasks** | Completed review tasks. |
| **Submissions** | Items with costs. |

Hold your mouse pointer over a column heading to see a short explanation, where one is available.

## How to export the costs

1. Set the dates, filters and **Group by** the way you want them.
2. Select **Export CSV** at the top right. The button shows **Exporting…** while it works.
3. A file is downloaded that you can open in a spreadsheet program.

## How to see how costs are worked out

1. Scroll to the bottom of the page.
2. Select **How costs are calculated** to open the explanation. Select it again to close it.

In short:

- **Model spend** is the amount of text processed multiplied by each model's price. The page uses standard US dollar list prices, or your organisation's own prices if they have been set, converted to your currency. A price change applies to past periods too.
- **Reviewer time** is the active reading time measured in the review workspace multiplied by an hourly rate: the reviewer's own rate, otherwise their team's, otherwise the organisation default.
- Decisions made outside this app have no reading activity, so an estimate is used and shown as estimated time.
- **Cost per submission** is the total divided by the submissions that had model use or a completed review in the range. Model use that isn't linked to a submission still counts toward the total.

## Common questions

**"Some model usage has no price, so it counts as zero."**
The page lists the models with no price. Until prices are added, their use is counted as costing nothing, so the total is too low. Prices are added in [Settings › Organisation](settings-organisation.md). If you are not an admin, ask one.

**"… of reviewer time has no hourly rate, so it costs nothing here."**
Some reviewers have no hourly rate. Set a rate on the reviewers or their teams (see [People & teams](settings-people-and-teams.md)), or a default rate in [Settings › Organisation](settings-organisation.md). If you are not an admin, ask one.

**"… of review time is estimated"**
Some decisions were made through a connected tool, where reading activity cannot be recorded. The page uses each task's effort estimate for those.

**"No model usage or completed reviews in this range. Try a longer range or clear the filters."**
There are no costs for your dates and filters. Widen the dates, or set **Workflow** and **Connected app** back to "All".

**"Choose a start and end date.", "The start date must be on or before the end date." or "The range can be at most two years."**
Correct the **From** and **To** dates. **Export CSV** is greyed out until the dates are valid.

**Where do I see volumes and outcomes rather than costs?**
See [Reports](reports.md).
