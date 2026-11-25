# Review workflows

A workflow sets how one kind of work is reviewed: who reviews it, how quickly, and which checks apply. Use this page to create workflows, change them, and see how each one has changed over time.

**Who can use this:** Oversight leads and admins.

**Where to find it:** Settings (bottom of the left menu) › Review workflows

## What you see on the page

The page lists every workflow in a table. If you have none, it says **No workflows yet.**

| Column | What it shows |
|---|---|
| **Workflow** | The workflow's name, its current version number (for example **v3**) and a short reference code. |
| **Stages** | The stages in order, joined by arrows. If any stage reassigns late work, it adds **reassigns on SLA breach**. |
| **Planted mistakes** | The share of work that gets a planted mistake, as a percentage. |
| **Spot checks** | The share of reviewed work that gets a spot check, as a percentage. |
| **Must-check warnings** | The warning levels a reviewer must acknowledge before deciding (low, medium, high), or **none**. |
| **Limits** | The daily limit on review time for each reviewer, in minutes per day. |
| **Min score** | The minimum review quality score. |
| **Apps** | The connected apps allowed to use the workflow, or **All**. |
| **Updated** | When the workflow was last saved. |

Each row has two buttons: **Edit** and **History**.

## How to create a workflow

1. Select **New workflow** at the top right. A form called **New workflow** opens above the table.
2. Type a **Name**. You cannot save without one.
3. Set up the **Stages**. A new workflow starts with one stage called **Review**. See [Stages](#stages) below.
4. Work through the other settings. Each one is explained in [The settings on the form](#the-settings-on-the-form). Most already hold a sensible starting value.
5. Select **Save**. A green message confirms it, for example "Saved Client letters as version 1."

To close the form without saving, select **Cancel**.

## How to edit a workflow

1. Find the workflow in the table and select **Edit**. The page scrolls to the form at the top.
2. The form heading tells you which version you are about to create, for example "Edit Client letters (saves as version 4)".
3. Change what you need.
4. Select **Save**.

Every save creates a new version. Work already sent keeps the version it arrived under, so your changes only affect work sent after you save.

## The settings on the form

### Stages

A stage is one step of the review. Work passes through the stages in the order shown. Each stage is one row with four settings.

| Field | What it means | Starting value |
|---|---|---|
| **Stage name** | What the step is called. Every stage needs a name before you can save. | **Review** for the first stage; **Stage 2**, **Stage 3** and so on for added stages |
| **Routed to** | The team whose reviewers get the work at this stage. Choose **Any reviewer** to leave it open. | **Any reviewer** |
| **SLA (hours)** | The deadline: how many hours the stage has to be finished. Leave it empty for no deadline (the box then shows **No SLA**). | 24 |
| **When the SLA is missed** | What happens if the deadline passes: **Escalate**, or **Reassign to another reviewer**. You cannot change this while the deadline is empty. | **Escalate** |

- To add a step, select **Add stage**.
- To take one out, select **Remove** on its row. **Remove** only appears when there is more than one stage.

Teams are set up in [People & teams](settings-people-and-teams.md).

### Planted mistakes and spot checks

| Field | What it means | Starting value |
|---|---|---|
| **Share of work given a planted mistake (0–1)** | How much of the work gets a planted mistake, written as a decimal: 0.05 means 5 in every 100 items. | 0.05 |
| **Subject area for planted mistakes** | The kind of content the planted mistakes should suit: **general**, **financial**, **insurance**, **professional services** or **legal**. | **general** |
| **Spot check rate (0–1)** | How much reviewed work gets a spot check, written as a decimal: 0.05 means 5 in every 100 items. | 0.05 |
| **Spot check team** | The team that carries out spot checks for this workflow. Choose **Any eligible reviewer** to leave it open. | **Any eligible reviewer** |

If you clear either rate, the box shows **Default 0.05** and that default is used.

### Must-check warnings

Under **Risk flags that must be acknowledged before a decision**, tick the warning levels a reviewer has to acknowledge before they can decide: **Low severity**, **Medium severity** and **High severity**. A new workflow has only **High severity** ticked.

If you untick all three, the form warns: "No flags are mandatory: reviewers can decide without acknowledging any."

### Reading standards

These settings say how much reading counts as a careful review, and what happens when a review falls short.

| Field | What it means | Starting value |
|---|---|---|
| **Min reading ratio** | The least time a reviewer must spend reading, as a share of the time the app expects the document to take: 0.3 means 30%. | 0.3 |
| **Min section coverage** | The least share of the document's sections the reviewer must have looked at: 0.6 means 60%. | 0.6 |
| **Below review standard** | What happens when a review falls below either figure. See the options below. | **Warn and confirm** |

The options for **Below review standard** are:

| Option | What it does |
|---|---|
| **Warn and confirm** | The reviewer is warned and must confirm before going ahead. |
| **Block approval** | The reviewer cannot approve the work. |
| **Allow and flag** | The decision goes through and is flagged. |

Under **Decisions accepted from**, tick the ways a decision may be made on this workflow: **Web workspace** (this app, in [To review](reviewing-work.md)), **API** and **MCP** (both are ways for other software to send a decision; ask your IT team whether you use them). All three are ticked unless you change them. At least one must stay ticked, so the last ticked box cannot be cleared.

The form notes: "Only the web workspace measures reading. Decisions made through the API or MCP have no reading activity recorded, so the standard above applies to them." If reading activity matters to you, tick only **Web workspace**.

### Limits

These settings protect reviewers from doing too much review in one go.

| Field | What it means | Starting value |
|---|---|---|
| **Daily cap (review minutes)** | The most minutes of review one reviewer is given in a day. | 240 |
| **Hourly cap** | The most minutes of review one reviewer is given in an hour. | 90 |
| **Team daily ceiling (minutes)** | The total review minutes per day for the stage's team on this workflow. Leave it empty for none; the team's own ceiling, if it has one, then applies. | Empty (**None**) |
| **When at capacity** | What happens to new work when reviewers have reached their limits. See the options below. | **Queue** |
| **Max consecutive from one source** | The most items in a row a reviewer is given from the same source before they get something different. It must be a whole number. | 3 |

The options for **When at capacity** are:

| Option | What it does |
|---|---|
| **Queue** | The work waits until a reviewer has room. |
| **Queue and escalate** | The work waits, and it is also escalated. |
| **Defer to source** | The work is sent back to whoever sent it, to deal with later. |

If you clear a limit that has a default, the box shows it (for example **Default 240**) and that default is used.

### Minimum review quality score

| Field | What it means | Starting value |
|---|---|---|
| **Minimum review quality score** | The lowest review quality score a review may have and still count as good enough. | 60 |
| **Below minimum score** | What happens when a review scores below the minimum. See the options below. | **Release with a flag** |

The options for **Below minimum score** are:

| Option | What it does |
|---|---|
| **Release with a flag** | The decision is sent back as normal, but it is flagged as below the minimum. |
| **Require a second review** | Another reviewer has to review the work. |
| **Block release** | The decision is not sent back until someone with oversight access deals with it. See [On hold](on-hold.md). |

### Connected apps that can use this workflow

This decides which connected apps may send work to the workflow.

- **All applications** is selected to begin with. The note underneath reads "Every application can submit to this workflow."
- To limit the workflow, select one or more app names. Selected apps show a tick. The note changes to, for example, "Only 2 selected applications can submit to this workflow."
- To open it up again, select **All applications**.
- An app marked **(inactive)** has been switched off in [Connected apps](settings-connected-apps.md). You can still select it.

If you have no connected apps, this section says **No connected apps yet.**

## How to see a workflow's history

1. Find the workflow in the table and select **History**. A list of versions opens under the row, newest first.
2. Read each line. It shows:
   - the version number (the current version is highlighted);
   - when it was saved and, where the app can show it, who saved it;
   - what changed from the version before, as "old value → new value". The first version says **Created**. A save that changed nothing says **Saved with no changes**.
3. Select **Hide history** to close the list.

The name of who saved each version comes from the [Activity log](activity-log.md). If you do not have access to that, the name is left out.

History is for reading only. There is no button to go back to an older version; to undo a change, edit the workflow and put the old values back.

## Deleting a workflow

This page has no way to delete or archive a workflow. If you no longer want a workflow used, you can limit which connected apps may send work to it (see [above](#connected-apps-that-can-use-this-workflow)).

## Common questions

**Why is Save greyed out?**
The workflow has no **Name**, or one of the stages has an empty **Stage name**. Fill them in.

**Why can't I change "When the SLA is missed"?**
That stage has no deadline. Type a number of hours in **SLA (hours)** first.

**Why is there no Remove button on my stage?**
A workflow must have at least one stage. **Remove** appears once there are two or more.

**I changed a workflow. Why is some work still following the old rules?**
Work already sent keeps the version it arrived under. Only work sent after your save uses the new version.

**I cleared a number and saved. What happens?**
An empty box is never treated as zero. The app uses its default for that setting, which the empty box shows in grey (for example **Default 60**). To switch something off, type 0 where that makes sense, for example a planted mistake share of 0.

**The Spot check team or Routed to list has no teams in it.**
No teams exist yet, or you do not have access to the list. Create teams in [People & teams](settings-people-and-teams.md).

**A red message appeared when I saved.**
The app rejected one of the values, and the message says which. Correct it and select **Save** again. Your other entries stay on the form.

**The history shows words I don't recognise.**
Changes to some settings are listed under their internal names (for example "min score" or "below action") rather than the labels on the form. They refer to the same settings described on this page.
