# Connected apps

Connected apps are the apps and tools that send work here for review, and where the results are sent back. Use this page to register them, manage their keys, link up systems such as SharePoint or Jira, and check that results are reaching them.

**Who can use this:** Integration owners and admins.

**Where to find it:** Settings (bottom of the left menu) › Connected apps

Most of the values on this page are technical. You will usually need your IT team, or the owner of the other system, to supply them.

## The three tabs

| Tab | What it is for |
|---|---|
| **Apps** | Register a connected app, manage its keys, and switch it off or on. The number beside the tab name is how many apps you have. |
| **Connectors** | Ready-made links to SharePoint / OneDrive, Microsoft Teams, Jira and Azure DevOps. Each connector acts as one of your connected apps. |
| **Delivery history** | A list of every result message sent back to your apps, and whether it arrived. |

## Apps

### How to register a connected app

1. Open the **Apps** tab.
2. Under **Register a connected app**, type a **Name**, for example "Drafting assistant".
3. If the app should be told about results automatically, type its **Webhook URL (optional)**. This is the web address in the other app where decisions and status changes are posted. It must be https on a public host. Your IT team or the app's owner will know it. If you leave it empty, the app has to ask for results itself.
4. Tick **Also enable OAuth client credentials** only if your IT team asks for it. It is a second way for the app to sign in.
5. Select **Create**.
6. A window opens showing the app's keys. **Copy these now: you won't see them again.** Use the **Copy** button beside each value and pass them to your IT team securely.
7. Tick **I have stored these somewhere safe**, then select **Done**.

The window shows:

| Value | What it is |
|---|---|
| **App ID** | The app's reference in Proof of Review. Not secret; you can copy it again later from the app's card. |
| **API key** | The password the app uses when it sends work here. |
| **Webhook signing secret** | A code the other app uses to check that result messages really came from Proof of Review. |
| **OAuth client ID** and **OAuth client secret** | Shown only if you ticked the OAuth box. The second sign-in name and password for the app. |

The **Using the API** box beside the form is a technical example for your IT team.

### Reading an app's card

Each app has a card under **Connected apps**. Select **Refresh** to reload the list.

| Item | What it shows |
|---|---|
| Name and ID | The app's name and App ID, with a **Copy ID** button. |
| Status | **active** or **deactivated**. An **OAuth** label appears if OAuth is on. |
| **API keys** | A list of every key the app can use. See [the key list](#how-to-read-an-apps-key-list). |
| **Webhook URL** | Where results are sent, or "none (the app polls for results)" if no address is set. |
| **OAuth client** | The OAuth client ID, or **not enabled**. |
| **Created** | When the app was registered. |

### How to change an app's name or where results are sent

1. On the app's card, select **Edit**.
2. Change the **Name** or the **Webhook URL**. Leave the address empty to stop sending results to the app. The signing secret does not change.
3. Select **Save**.

### How to read an app's key list

An API key is the password a tool uses when it sends work here. An app can have several keys working at the same time, so you can give one to each tool or person and take one away without disturbing the others.

| Column | What it shows |
|---|---|
| **Name** | What the key is for. The key made when the app was registered is called **Original key**. |
| **Starts with** | The first few characters of the key, so you can match it to the one a tool is using. The full key is never shown again. |
| **Created** | When the key was made. |
| **Last used** | When the key was last used, or **never**. The original key shows **not tracked**. |
| **Expires** | When the key stops working by itself, **never**, or an **expired** label. |

### How to create another API key

Do this when a new tool or person needs access, or when a tool reports that its key was rejected.

1. On the app's card, select **Create API key**.
2. Under **What is this key for?**, type a name, for example the tool or person who will use it.
3. Under **When should it stop working?**, choose **Never**, **30 days**, **90 days** or **1 year**.
4. Select **Create key**.
5. Copy the **API key** from the window, tick **I have stored these somewhere safe**, and select **Done**. The key is shown only this once.
6. Give the key to whoever looks after the tool.

The new key works straight away. The keys the app already has keep working. An app can have up to 20 extra keys.

### How to revoke a key

1. In the app's key list, find the key by its **Name** or **Starts with**.
2. Select **Revoke**, then **Revoke key** to confirm.

The key stops working immediately and disappears from the list. Anything still using it is refused. The app's other keys are not affected. This can't be undone; create a new key if you revoked the wrong one.

### How to replace the original key

The original key can't be revoked, only replaced.

1. In the app's key list, beside **Original key**, select **Replace**.
2. Choose how long the current original key keeps working:

   | Choice | When to use it |
   |---|---|
   | **None** | The old key stops working immediately. Use this if the key may have leaked. |
   | **24 hours** | Time to redeploy the app with the new key. This is selected to begin with. |
   | **72 hours** | For apps with a slower release cycle. |
   | **7 days** | The longest allowed overlap. |

3. Select **Replace key** (or **Replace and revoke old key** if you chose **None**).
4. Copy the new **API key** from the window, tick **I have stored these somewhere safe**, and select **Done**.

Keys you created separately are not affected. While the old original key is still in its overlap, the list shows a row **Original key (before it was replaced)** with the time it works until. Select **Revoke now** on that row to stop it early.

### How to replace the webhook secret

1. On the app's card, select **Rotate webhook secret**.
2. Select **Rotate secret** to confirm.
3. Copy the new **Webhook signing secret**, tick **I have stored these somewhere safe**, and select **Done**.

Result messages use the new secret straight away. Update the receiving app at the same time, or it will reject messages until it has the new secret. Messages that fail are retried, and you can send them again from **Delivery history**.

### How to turn OAuth on or off, or replace its secret

OAuth is a second way for an app to sign in, using short-lived passes instead of the API key alone. Only use it if your IT team asks.

- **To turn it on:** select **Enable OAuth**, then **Enable OAuth** to confirm. Copy the **Client ID** and **Client secret** shown. The secret is shown once.
- **To replace the secret:** select **Rotate OAuth secret**, then **Rotate secret**. The old client secret stops working immediately for new sign-ins. The client ID stays the same.
- **To turn it off:** select **Disable OAuth**, then **Disable OAuth** to confirm. New sign-ins are refused at once and existing ones expire within the hour. The API key keeps working. Turning OAuth on again creates a new client ID and secret.

### How to switch an app off or on

1. On the app's card, select **Deactivate**.
2. Select **Deactivate** to confirm.

While an app is deactivated, anything it sends is refused and its connectors stop sending work. Nothing is deleted. To bring it back, select **Activate**; a green message confirms the app "is active again".

There is no button to delete an app.

## Connectors

A connector links Proof of Review to another system without any programming. Each connector works on behalf of one connected app, so register an app first.

### How to add a connector

1. Open the **Connectors** tab and select **Add connector**.
2. Choose the type of connector. The types offered come from the server; this guide covers the four the screen has forms for.
3. Choose the **Connected app** the connector will act as. You cannot change this later.
4. Fill in the fields for that type (see the subsections below). Fields marked * are required. Open **More settings** for the less common ones.
5. Leave **Connector is on** ticked to start it straight away, or untick it to set it up now and start it later.
6. Select **Create connector**.

Under each type's name you will see **Polls**, **Events, optional polling** or **Events**. "Polls" means the connector checks the other system for new work at regular intervals. "Events" means it only acts when something happens here, such as a decision.

Secret values (passwords, tokens) are hidden after saving. When you edit, the box says "Stored (hidden). Type a new value to replace it." Leave it empty to keep the stored value.

### SharePoint / OneDrive folder

Documents dropped in the inbox folder are submitted for review. When decided, the decision is written back as file metadata and the file moves to the matching folder.

| Field | What it is, and who knows it |
|---|---|
| **Microsoft Entra tenant ID** | Your organisation's Microsoft 365 identifier. Your IT team knows it. |
| **App registration client ID** | The ID of a Microsoft Entra app registration that is allowed to read and write files in the drive. Your IT team creates this. |
| **Client secret** | The password for that app registration. From your IT team. |
| **Drive ID** | The document library or OneDrive drive to watch. Your IT team can look it up. |
| **Workflow** | The [review workflow](settings-review-workflows.md) that files are sent to. |
| **Risk tier for submitted files** | The risk given to every file: low, medium or high. Starts at medium. |

Under **More settings**:

| Field | What it is | Starting value |
|---|---|---|
| **Inbox folder** | The folder watched for new documents. | Review/Inbox |
| **Approved folder** | Where approved files are moved. | Review/Approved |
| **Rejected folder** | Where rejected files are moved. | Review/Rejected |
| **Changes requested folder** | Where files needing changes are moved. | Review/Changes requested |
| **Deferred folder** | Where deferred files are moved. Use the inbox folder to leave deferred files where they are. | Review/Deferred |
| **Cancelled folder** | Where cancelled files are moved. | Review/Cancelled |
| **Most files read per run** | A whole number from 1 to 5000. Leave empty for 500. | 500 |
| **Content origin** | Who wrote the documents: AI, a person (human), or a mix (mixed). | mixed |

### Microsoft Teams notifications

Posts cards to a Teams channel when reviewers are assigned work and when items are escalated or deferred. Decisions always happen in the review workspace, not in Teams.

| Field | What it is, and who knows it |
|---|---|
| **Teams webhook URL** | The address Teams gives you for posting into a channel (a Teams Workflows / Power Automate or incoming-webhook URL). Whoever manages the Teams channel can create it. It is stored encrypted. |
| **Reviewer app address (optional)** | Where the buttons on the cards link to. Leave empty to use the app's normal address. |
| **Post a card when** | Tick the events to announce: **A reviewer is assigned an item**, **An item is escalated**, **An item is deferred**, **A review task has used 75% of its SLA** (its deadline). All four start ticked. |

If you untick every event, the form warns: "No events selected: this connector will not post anything."

### Jira

Decisions on linked submissions comment on the ticket, move it and attach the review record. With a JQL query, matching tickets are submitted automatically.

| Field | What it is, and who knows it |
|---|---|
| **Jira site URL** | Your Jira address, for example https://your-company.atlassian.net. |
| **Account email** | The email of the Jira account the connector works as. Your Jira administrator chooses it. |
| **API token** | An Atlassian API token for that account. Your Jira administrator creates it. |
| **Submit tickets matching (JQL, optional)** | A Jira search (JQL is Jira's search language) that picks the tickets to send for review. Your Jira administrator can write it. Leave empty to only update tickets that were linked when the work was sent. Move tickets out of the matching status once submitted. |
| **Workflow** | The review workflow tickets are sent to. Needed when a JQL query is set. |
| **Risk tier for submitted tickets** | The risk given to every ticket: low, medium or high. Starts at medium. |

Under **More settings**:

| Field | What it is |
|---|---|
| **Transitions after a decision (JSON, optional)** | Which Jira transition to apply for each decision, written as a small block of text your Jira administrator can supply (the box shows an example). Leave empty to only comment. |
| **Most tickets read per run** | A whole number from 1 to 5000. Leave empty for 500. |

### Azure DevOps

Decisions on linked work items add a history entry, move the item and attach the review record. With a WIQL query, matching work items are submitted automatically.

| Field | What it is, and who knows it |
|---|---|
| **Organization** | Your Azure DevOps organisation name. |
| **Project** | The Azure DevOps project name. |
| **Personal access token** | A token that lets the connector read and write work items. It needs Work Items (read & write). Your Azure DevOps administrator creates it. |
| **Submit work items matching (WIQL, optional)** | An Azure DevOps search (WIQL is its search language) that picks the work items to send for review. Leave empty to only update work items that were linked when the work was sent. |
| **Workflow** | The review workflow work items are sent to. Needed when a WIQL query is set. |
| **Risk tier for submitted work items** | The risk given to every work item: low, medium or high. Starts at medium. |

Under **More settings**:

| Field | What it is |
|---|---|
| **States after a decision (JSON, optional)** | Which state to move the work item to for each decision, written as a small block of text your administrator can supply (the box shows an example). Leave empty to only add a history entry. |
| **Server URL (optional)** | Only for Azure DevOps Server or a custom domain. |
| **Most work items read per run** | A whole number from 1 to 5000. Leave empty for 500. |

### Reading the connectors table

| Column | What it shows |
|---|---|
| **Connector** | The type, a short summary of its settings (such as the folder and the workflow) and a reference code. |
| **App** | The connected app it acts as. |
| **Status** | **on** or **off**. |
| **Polling** | **polls** if it checks the other system for new work; otherwise **events only**. |
| **Last run** | When it last checked, or **never**. |
| **Last error** | The most recent problem, if any. Hover over it to read the full text. |

### How to test a connector (Run now)

**Run now** appears only on connectors that poll, and only works while the connector is on.

1. Select **Run now** on the connector's row.
2. Read the result window. It tells you how many new items were submitted for review and, where it applies, how many decisions were written back.
3. Select **Close**.

"Nothing new matched. Items already submitted are not submitted again." is a normal result when there is no new work.

### How to pause, change or delete a connector

- **Pause:** select **Switch off**. Select **Switch on** to start it again.
- **Change:** select **Edit**, make your changes and select **Save changes**.
- **Delete:** select **Delete**, then **Delete connector** to confirm. Its settings and stored secrets are removed and cannot be recovered. Work it already sent is kept. To pause it instead, switch it off.

## Delivery history

The **Delivery history** tab shows the **Webhook delivery log**: every result message posted to your apps' webhook URLs. Failed messages are retried automatically, with longer gaps between tries.

### How to check whether results reached an app

1. Open the **Delivery history** tab.
2. Narrow the list with **App**, **Status** (pending, delivered, failed) and **Event**. Select **Clear filters** to start again.
3. Read the rows. The list shows 25 at a time; use **Previous** and **Next** to move through it. Select **Refresh** to reload.

| Column | What it shows |
|---|---|
| **Event** | What happened. See the table below. |
| **App** | The connected app the message was sent to. |
| **Submission** | A short reference for the piece of work. |
| **Status** | **pending** (waiting, with the time of the next try), **delivered** (with the time) or **failed**. |
| **Attempts** | How many times the app has tried to send it. |
| **Last response** | What the other app answered, for example "HTTP 200". Numbers of 400 and above mean it was refused and are shown in red. "no response" means the other app could not be reached. |
| **Created** | When the message was first created. |

| Event | Meaning |
|---|---|
| submission.received | The work arrived. |
| submission.in_review | A review has started. |
| submission.decided | A decision was made. |
| submission.deferred | The work was deferred. |
| submission.escalated | The work was escalated. |
| submission.cancelled | The work was cancelled. |

### How to send a message again

1. Find the row and select **Redeliver**.
2. Read the message at the top. Green text says it was accepted. Otherwise it says the redelivery did not succeed and "it will be retried".

You can redeliver a message that was already delivered. The other app should ignore the duplicate.

## Common questions

**"No connected apps yet. Register one above to get an API key."**
You have not registered an app. Follow [How to register a connected app](#how-to-register-a-connected-app).

**A tool says its key was rejected.**
The key has been replaced, revoked or has expired, or the app is deactivated. Check the app is **active**, compare the first characters of the tool's key with **Starts with** in the key list, and if there is no match, create a new key and give it to the tool.

**I closed the window before copying the key.**
Keys and secrets are shown only once. For an API key, create another key and revoke the one you lost. For a secret, rotate it to get a new one. If you try to close the window without ticking the box, the app asks "Close without confirming you stored these? They can't be shown again."

**Add connector is greyed out.**
You have no connected apps yet. The page says "Register a connected app first: connectors submit and receive decisions as one of your apps."

**"Fix the highlighted settings first."**
A connector field is wrong. Look for **Required.**, "Enter a full URL starting with https://", or a "Not valid JSON" message under a field.

**"This workflow only accepts submissions from other apps…"**
The workflow you picked is limited to other connected apps (the list marks it "(not open to this app)"). Pick another workflow, or add the app to the workflow in [Review workflows](settings-review-workflows.md).

**"This app is deactivated, so the connector can't submit until it is activated."**
Activate the app on the **Apps** tab.

**Run now is greyed out.**
The connector is off. Switch it on first.

**"Nothing ran: the connector is off or has no query to poll."**
Switch the connector on. For Jira or Azure DevOps, also add a query in the JQL or WIQL field.

**"The run failed: …"**
The other system refused or could not be reached. The message gives the reason. Check the values with your IT team, then try **Run now** again.

**"No deliveries yet." or "No deliveries match these filters."**
Nothing has been sent yet, or your filters are too narrow. Apps with no webhook URL never appear here, because they collect results themselves.

**A delivery keeps failing.**
Check the app's **Webhook URL** is right. If you recently rotated the webhook secret, make sure the other app has the new one. Then select **Redeliver**.
