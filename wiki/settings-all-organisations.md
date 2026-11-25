# All organisations

This page lists every organisation on the platform. Each organisation has its own people, workflows, data and settings. Use it to create a new organisation, rename one, or switch one off and on.

**Who can use this:** Platform administrators only. Most admins will not see this tab.

**Where to find it:** Settings (bottom of the left menu) › All organisations

## How to read the page

Four boxes at the top give totals across the whole platform:

| Box | What it shows |
| --- | --- |
| **Organisations** | How many organisations there are, and how many of them are deactivated. |
| **Active people** | How many people can currently sign in, across all organisations. |
| **Submissions** | How many pieces of work have been sent for review in total. |
| **Open review tasks** | How many reviews are waiting to be done. |

Below that is a table with one row for each organisation:

| Column | What it shows |
| --- | --- |
| **Organisation** | The name, its data region, the date it was created and its ID. Your own organisation is marked **yours**. |
| **Status** | **active** or **deactivated**. |
| **Admins** | The email addresses of its active admins. "no active admin" means nobody can currently manage it. |
| **People** | How many people can sign in. |
| **Submissions** | How many pieces of work it has sent for review. |
| **Open tasks** | How many reviews are waiting. |
| **Connected apps** | How many connected apps it has. |
| **SSO** | **set up** if the organisation has single sign-on settings, otherwise "–". |

## How to find an organisation

1. Type in the **Search** box. You can search by name, admin email or ID.
2. Untick **Show deactivated** if you only want to see active organisations.
3. Click **Refresh** to load the latest figures.

## How to create an organisation

1. Click **New organisation** at the top right.
2. Type the **Organisation name**.
3. In **Data region**, pick or type where its data should be held. The suggestions are **uk**, **eu** and **us**, and it starts on **uk**.
4. Under **First admin**, type the **Email** of the person who will manage the new organisation. Their **Name** is optional.
5. Optionally type a **Password** for them (at least 12 characters).
6. Click **Create organisation**.

A green message confirms the organisation was created and names its first admin.

- If you set a password, the admin can sign in with it. Share it with them securely.
- If you left the password empty, the admin signs in with single sign-on once it is set up, or with a password reset link.

The first admin can then add their own people in [People & teams](settings-people-and-teams.md) and adjust their settings in [Organisation](settings-organisation.md).

## How to rename an organisation

1. Click **Rename** on the organisation's row.
2. Type the new **Organisation name**.
3. Click **Rename**, or **Cancel**.

You see "Renamed to …" when it is done.

## How to deactivate an organisation

Deactivating switches an organisation off without deleting anything.

1. Click **Deactivate** on the organisation's row.
2. Read the warning. It tells you:
   - how many people can no longer sign in;
   - how many connected apps will have their requests refused at once;
   - how many open review tasks will stop progressing, if there are any.
3. Click **Deactivate** to confirm, or **Cancel**.

Nothing is deleted. Activate it again to restore access.

You can't deactivate your own organisation. The button is greyed out on the row marked **yours**.

## How to activate an organisation again

1. Make sure **Show deactivated** is ticked so you can see it.
2. Click **Activate** on the organisation's row.
3. Click **Activate** to confirm.

Its people can sign in again and its connected apps can make requests again.

## What this page does not do

- It does not delete an organisation.
- It does not change an organisation's data region after it has been created.
- It does not let you manage another organisation's people or settings. Those are managed by that organisation's own admins.

## Common questions

**"Enter a valid email address."**
The first admin's email is missing or not written correctly.

**"At least 12 characters (or leave empty)."**
The password is too short. Make it longer, or clear the box so the admin uses single sign-on or a reset link.

**"Required."**
The organisation name or data region is empty.

**"No organisations match."**
Nothing fits your search. Clear the **Search** box and tick **Show deactivated**.

**"No organisations yet."**
None have been created. Click **New organisation**.

**"Organisations could not be loaded."**
Something went wrong fetching the list. Click **Refresh**. If it keeps failing, contact whoever runs the platform.

**An organisation shows "no active admin".**
Nobody in that organisation can currently manage it. This page does not offer a way to add an admin to an existing organisation.

**Why is "Deactivate" greyed out?**
That row is your own organisation. You can't deactivate it.
