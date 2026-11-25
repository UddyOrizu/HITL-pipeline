# Organisation

This page holds the settings that apply to your whole organisation: its name, reviewing rules, how long content is kept, costs, email and sign-in.

**Who can use this:** Admins only.

**Where to find it:** Settings (bottom of the left menu) › Organisation

The page has six sections: **General**, **Reviewing**, **Data retention**, **Costs**, **Email notifications** and **Single sign-on**. Click a name in the list at the side to jump to that section.

Each section saves on its own. If you change something and have not saved it yet, the words "Unsaved changes" appear beside that section's save button.

## How to change your organisation's name

1. Go to the **General** section.
2. Type the new name in **Organisation name**.
3. Click **Save**. You see "Name saved."

This section also shows three things you cannot change here:

| Item | What it is |
| --- | --- |
| **Organisation ID** | A unique code for your organisation. Support staff may ask you for it. |
| **Data region** | Where your organisation's data is held. |
| **Status** | **active** or **deactivated**. |

## How to let admins review work

Admins normally don't review. An admin sets the controls that reviewers are measured by (workflows, assignment, planted mistakes, spot checks and minimum scores) and can see and change the records those controls produce. If the same person also reviews, they could mark their own homework, and the review record would no longer show an independent check. Keeping the roles apart is what auditors expect.

Allow it only when your organisation is too small for separate people.

1. Go to the **Reviewing** section.
2. Tick **Allow people to have both the admin and reviewer roles**.
3. Read the warning: "Turning this on weakens separation of duties. It is recorded in the activity log."
4. Click **Save**.
5. Go to [People & teams](settings-people-and-teams.md), edit the admin and tick **Also a reviewer**.

Ticking the box here does not make any admin a reviewer by itself. Each admin still needs the reviewer role given to them, and their reviews are measured and logged like anyone else's.

## How to keep admins and reviewers separate again

1. First go to [People & teams](settings-people-and-teams.md) and remove either the admin or the reviewer role from everyone who has both.
2. Come back to the **Reviewing** section and untick **Allow people to have both the admin and reviewer roles**.
3. Click **Save**. You see "Admins and reviewers are kept separate."

If someone still has both roles, the change is not saved. A notice headed "Still both admin and reviewer:" may list the people concerned.

## How to choose how long content is kept

This setting controls how long the app keeps the content people sent for review once it is finished (approved, rejected, changes requested, deferred or cancelled). The time is counted from the release date.

| Removed | Kept |
| --- | --- |
| Uploaded files (originals) and the extracted document text | Decisions and their reasons |
| Claim text and rationale in risk flags | Review quality scores |
| Planted mistake text, reviewer comments and suggested edits | The activity log |
| Reading activity | The review record, so you can still prove the review happened |

1. Go to the **Data retention** section.
2. Choose one of the two options:
   - **Keep content until removed**. Nothing is removed automatically.
   - **Remove content after … days**. Type the number of days. It must be a whole number, 30 or more. Content past this age is removed automatically.
3. Click **Save**.

## How to remove one submission's content now

Use this for an erasure request from a client. **This can't be undone.**

The submission must be finished. Decisions, scores, the activity log and the review record are kept.

1. Go to the **Data retention** section and find **Remove one submission's content now**.
2. Type or paste the **Submission ID**. This page does not say where to find it. If you don't have it, ask whoever raised the request, or look the work up in [Review records](review-records.md).
3. Click **Remove content…**
4. A window asks you to confirm that you want to permanently remove the files, text, comments and reading activity of that submission. Check the ID shown is the right one.
5. Click **Remove content permanently**, or **Cancel**.

A green message tells you how many files were deleted. The removal is recorded in the [activity log](activity-log.md).

## How to set up costs

These settings are used by the cost reports (see [Costs](costs.md)). The reports add together two things: what the AI models cost (priced in US dollars and converted to your currency) and reviewer time (measured minutes multiplied by an hourly rate).

1. Go to the **Costs** section.
2. In **Currency**, type or pick a three-letter code such as GBP.
3. In **Exchange rate**, type how much of your currency one US dollar buys. If your currency is USD, this is fixed at 1.
4. In **Default reviewer hourly rate**, type the rate to use for reviewers who have no rate of their own and whose team has none. Leave it empty to leave reviewer time unpriced.
5. Click **Save**. You see "Cost settings saved. Cost reports use them from now on."

Rates for individual people and teams are set in [People & teams](settings-people-and-teams.md).

## How to change the price used for an AI model

The app comes with default prices. You only need this if your organisation pays a different price. Your IT team or whoever manages the AI provider's bill will know the figures.

Prices are in US dollars per million tokens. ("Tokens" are the units AI providers charge by.)

1. In the **Costs** section, click **Default prices** to open the list. The date the prices were last updated is shown beside it.
2. Click **Override** next to the model whose price you want to change. A row for it appears under **Model price overrides**, filled in with the default figures.
3. Change **Input** and **Output**. Both are required.
4. Fill in **Cache read** and **Cache write** only if your provider charges for them separately. Otherwise leave them empty.
5. Click **Save**.

To add a model that is not in the default list, click **Add a model** and type its **Model ID** yourself. A model ID also matches longer IDs that contain it.

To go back to the default price, click **Remove** on that row and then **Save**.

## How to choose which emails are sent

1. Go to the **Email notifications** section.
2. Tick or untick each kind of email:

| Email | Who gets it and when |
| --- | --- |
| **Task assigned** | A reviewer gets an email when a new item is assigned to them. |
| **Escalations** | Oversight leads hear about escalations: missed deadlines, reviewer disagreements, capacity and release checks. |
| **Decision made** | The person who sent a submission from the web app hears when it has been decided. |

3. Click **Save**. You see "Email notification settings saved."

Each person can still turn email off for their own account.

## How to set up single sign-on

Single sign-on lets people sign in with the account they already use at work (for example Microsoft Entra ID, Okta or Google Workspace) instead of a separate password. People whose email domain you list are sent to your provider from the sign-in page.

You will need your IT team for this. They hold the values below.

1. Go to the **Single sign-on** section. **Status** shows **not set up**, **off** or **on**.
2. Copy the **Redirect URI to register with your provider** using the **Copy** button and send it to your IT team. They add it to the app registration in your sign-in provider as a single-page application redirect URI. It must match exactly.
3. Click **Set up single sign-on**.
4. Leave **Single sign-on is on** ticked.
5. Fill in the fields (see the table below). **Issuer URL** and **Client ID** are required.
6. Under **Roles for new people on their first sign-in**, tick the roles a new person should get automatically. Leave all unticked to let only people who already have an account sign in. Admin can't be given this way.
7. Click **Save single sign-on**. You see "Single sign-on saved and switched on."

| Field | What to put there |
| --- | --- |
| **Issuer URL** | The web address that identifies your sign-in provider, for example `https://login.microsoftonline.com/<tenant id>/v2.0`. Your IT team will know it. |
| **Client ID** | The identifier your sign-in provider gives to this app when IT registers it. |
| **Client secret** | A password-like value from the same registration. It is optional for some set-ups; your IT team will know whether you need one. It is stored encrypted and never shown again. |
| **Email domains** | The part after the @ in your staff email addresses, for example `example.com, example.co.uk`. Separate with commas. A domain can belong to only one organisation. |
| **Require this provider for everyone in the organisation** | When ticked, single sign-on for your people goes only through the provider set up here, and not through the platform's own sign-in provider. |
| **Scopes and endpoint overrides** | Extra settings (**Scopes**, **Authorization endpoint**, **Token endpoint**, **JWKS URI**) that most organisations leave empty. Only fill these in if your IT team asks you to. |

## How to pause single sign-on

1. In the **Single sign-on** section, untick **Single sign-on is on**.
2. Click **Save single sign-on**.

The settings are kept, but nobody is sent to the provider. People sign in with a password.

## How to remove single sign-on

1. In the **Single sign-on** section, click **Remove single sign-on**.
2. Read the warning, then click **Remove single sign-on** again to confirm, or **Cancel**.

This deletes the provider settings, including the stored client secret. People who only sign in with single sign-on won't be able to sign in until they set a password (from a reset link) or single sign-on is set up again. If you only want a break, pause it instead.

## Common questions

**The save button is greyed out.**
Nothing in that section has changed, or a value is not valid. Look for a red message under the field.

**"Enter a whole number of days, 30 or more."**
The number of days for removing content must be a whole number of at least 30.

**"Submission … had already been removed."**
That submission's content was removed earlier. Nothing more was done.

**"Currency must be a three-letter code such as GBP."**
Type the standard three-letter code for your currency.

**"Enter how many … one US dollar buys."**
Fill in the **Exchange rate** with a number above zero.

**"Fix the highlighted price rows."**
One of the rows under **Model price overrides** is incomplete. The row tells you what is wrong: "Enter a model ID.", "This model is listed twice.", "Input and output prices are required." or "Prices must be numbers, 0 or more."

**"Fix the highlighted settings first."**
A single sign-on field is missing or not valid. **Issuer URL** and **Client ID** are required, web addresses must be complete (starting with https://), and each email domain must be a proper domain name.

**A notice says the platform sends sign-ins back to a different address.**
You opened the app at an address the platform is not set up to use for single sign-on. Pass the notice to whoever runs the platform for you.

**The page shows an error and a "Try again" button.**
The settings could not be loaded. Click **Try again**. If it keeps failing, contact whoever runs the platform for you.
