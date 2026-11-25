# People & teams

This page lists everyone who can sign in to your organisation and what each person is allowed to do. It also lists your teams, which are the groups that review work is sent to.

**Who can use this:** Admins can see and change everything. Oversight leads can see the page but cannot change anything.

**Where to find it:** Settings (bottom of the left menu) › People & teams

## What an oversight lead sees

If you are an oversight lead but not an admin, the page is marked **read-only** and shows this message: "You can see users and teams because you're an oversight lead. Only admins can add, change, sign out or erase users."

You can still:

- search and filter the list of people;
- click **View** on a person to see their access, teams, hourly rate, open review tasks and recent changes;
- see the **Teams** table.

You will not see the **Add user**, **Add team**, **Edit**, **Deactivate**, **Reset password** or **Erase personal data…** buttons. The rest of this page describes what an admin can do.

## How to find a person

1. Look at the four boxes at the top for a quick count: **Admins**, **Reviewers**, **Other staff** and **Deactivated**.
2. Use the buttons above the list to narrow it down: **All**, **Admins**, **Reviewers**, **Other staff** or **Deactivated**.
3. Or type in the **Search name or email** box.

People who have been deactivated only appear under **Deactivated**. They are kept so that the activity log still makes sense.

### What the columns mean

| Column | What it shows |
| --- | --- |
| **User** | The person's name and email. It also shows **password** or **SSO only** (how they sign in) and their hourly rate if they have their own. "(you)" marks your own account. |
| **Access** | A label for each kind of access they have, plus **Deactivated**, **erased** or **Locked** where that applies. |
| **Teams** | The teams they belong to, or "–" if none. |
| **Review workload** | For reviewers: how many review tasks are open, minutes spent reviewing today, the share of planted mistakes they caught (with the number scored in brackets) and how many reviews they finished in the last 30 days. Everyone else shows "Not a reviewer". |
| **Last sign-in** | When they last signed in, or "Never". |

## How to add a person

1. Click **Add user** at the top right.
2. Type their **Email (used to sign in)** and their **Name**.
3. Choose an **Account type** (see the table below). New people start as **Reviewer**.
4. If you chose **Reviewer** or **Other staff**, tick any boxes they need under **Additional access**.
5. Under **Teams**, click each team they should belong to. A tick appears on the teams you have chosen. If you only have one team, it is chosen for you.
6. Optionally fill in **Hourly rate**. Leave it empty to use their team's rate, or the organisation's rate when the team has none. The rate is only used for [Costs](costs.md) reports.
7. Under **Sign-in**, either type an **Initial password** (at least 12 characters) or leave it blank if they will sign in with single sign-on only.
8. Click **Add user**.

A green message confirms the person was added and how they can sign in. The app does not show a step for emailing the new person, so tell them yourself. If you set a password, give it to them securely.

### Account types

| Account type | What it means |
| --- | --- |
| **Reviewer** | Reviews the work assigned to them. Their reviews are checked with planted mistakes, reading activity and spot checks. |
| **Admin** | Looks after people, teams and settings. Can do everything except review work. |
| **Other staff** | No review work and no admin rights. Pick the access they need under **Additional access**. |

### Additional access

These tick boxes appear for reviewers and other staff. Admins already have all of them.

| Access | What it means |
| --- | --- |
| **Submitter** | Sends work for review and follows the decision. Never reviews their own work. |
| **Oversight lead** | Watches review quality, decides on work that is on hold and sets up review workflows. |
| **Auditor** | Can read review records and the activity log. Can't change anything. |
| **Integration owner** | Sets up the apps and tools that send work here for review. |

Every person needs an account type or at least one kind of access. If they have neither, you see "Pick an account type or at least one kind of access." and cannot save.

## How to let an admin review as well

Normally admins are never given review work, because an admin sets the rules that reviewers are checked by.

1. First turn on **Allow people to have both the admin and reviewer roles** in [Organisation](settings-organisation.md).
2. Come back to **People & teams** and click **Edit** next to the admin.
3. Tick **Also a reviewer**.
4. Click **Save changes**.

That admin now gets review work and is checked with planted mistakes, reading activity and spot checks like any other reviewer.

## How to change a person's details or access

1. Click the person's row, or the **Edit** button at the end of it.
2. Change their **Name**, **Account type**, **Additional access**, **Teams** or **Hourly rate**. The email address cannot be changed.
3. Click **Save changes**.

If you take the reviewer role away from someone who has open review tasks, a warning tells you how many there are and that "Saving hands these to other reviewers." After saving, a green message tells you how many tasks were reassigned and how many are waiting for a free reviewer.

The lower part of the window shows **Open review tasks** for that person and, if there are any, a **Recent changes** list you can click to open. It shows what changed on the account, when, and who did it.

You cannot remove your own admin access.

## How to set or reset someone's password

1. Click **Edit** next to the person.
2. Under **Sign-in**, read the line that tells you whether they have a password, are single sign-on only, or are locked after failed attempts.
3. Type a **New password** (at least 12 characters).
4. Click **Reset password** (or **Set password** if they have never had one).
5. Give the new password to the person securely.

Setting a password signs the person out everywhere else and unlocks an account that was locked after failed attempts.

To change your own password, use **Change password** in the left menu instead.

## How to sign someone out everywhere

Use this if a person has lost a device or you think someone else is using their account.

1. Click **Edit** next to the person.
2. Under **Sign-in**, click **Sign out of all sessions**.
3. Click **Sign them out** to confirm, or **Cancel**.

They can sign in again straight away. To stop them signing in, deactivate them instead.

## How to deactivate a person

Deactivate someone when they leave or should no longer use the app.

1. Click **Edit** next to the person.
2. Click **Deactivate** at the bottom left.
3. Read the warning. If they have open review tasks, it tells you how many will be handed to other reviewers. The tasks are listed under **Open review tasks**.
4. Click **Confirm deactivation**, or **Keep active** to change your mind.

The person is signed out and can no longer sign in. Their past decisions stay in the records. You cannot deactivate yourself.

## How to reactivate a person

1. Click the **Deactivated** filter above the list.
2. Click **Edit** next to the person.
3. Click **Reactivate**.

## How to erase a person's personal data

Use this only when someone has asked for their personal data to be erased. It cannot be undone.

1. Deactivate the person first (see above). The erase option only appears for deactivated accounts.
2. Click the **Deactivated** filter and click **Edit** next to the person.
3. Under **Erasure request**, click **Erase personal data…**
4. Read the warning, then click **Erase personal data**, or **Keep** to cancel.

What happens:

- Their name, email, sign-in details and hourly rate are removed and can't be recovered.
- Their review decisions stay in the record, attributed to an anonymised "Erased user" account.
- Earlier [activity log](activity-log.md) entries are not rewritten. They keep what they recorded at the time.
- The account is marked **erased**. It can't be reactivated or edited afterwards.

You cannot erase your own account.

## How to create a team

Teams are the groups that work is sent to. Each stage of a [review workflow](settings-review-workflows.md) sends its work to a team.

1. Scroll down to **Teams** and click **Add team**.
2. Type the **Team name**.
3. Optionally fill in **Daily ceiling (minutes)**. This caps the whole team's review minutes for a day. Leave it empty for no ceiling.
4. Optionally fill in **Hourly rate**. It is used in cost reports for team members who do not have their own rate. Leave it empty to use the organisation's rate.
5. Click **Save**.

## How to change a team

1. In the **Teams** table, click **Edit** next to the team.
2. Change the name, daily ceiling or hourly rate.
3. Click **Save**, or **Cancel**.

The page does not offer a way to delete a team.

## How to add people to a team or remove them

Team membership is set on each person, not on the team.

1. Click **Edit** next to the person.
2. Under **Teams**, click a team to add them. Click it again to remove them.
3. Click **Save changes**.

### What the team columns mean

| Column | What it shows |
| --- | --- |
| **Team** | The team's name. |
| **Reviewers** | How many reviewers are in the team. |
| **Members** | How many people are in the team in total. |
| **Daily ceiling** | The most review minutes the whole team can do in a day, or "none". |
| **Hourly rate** | The team's rate for cost reports, or "organisation rate" if it uses the rate set in [Organisation](settings-organisation.md). |

## Common questions

**The page says "read-only". Why?**
You are an oversight lead, not an admin. Only admins can change people and teams.

**Why can't I tick "Also a reviewer" for an admin?**
Your organisation does not allow it yet. Turn on **Allow people to have both the admin and reviewer roles** in [Organisation](settings-organisation.md).

**I see "Your organisation no longer allows admins to review."**
This person still has both roles from before the rule was switched off. Other changes save without touching this. Untick **Also a reviewer** to separate the roles.

**A person shows "Locked".**
Their account was locked after failed sign-in attempts. Setting a new password unlocks it.

**I can't find the "Erase personal data…" button.**
It only appears once the person has been deactivated, and never on your own account.

**The window says "This user's personal data was erased."**
Their decisions stay in the record under an anonymised account, which nobody can change.

**The list says "No users match."**
Nobody fits the filter and search you have chosen. Click **All** and clear the search box.

**The Teams table says "No teams yet."**
Click **Add team** to create your first one.

**What currency are hourly rates in?**
Your organisation's currency, which an admin sets under **Costs** in [Organisation](settings-organisation.md).
