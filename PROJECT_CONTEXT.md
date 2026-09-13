# CoA Codex --- Project Context

**Last updated:** 2026-09-13\
**Repository:** `https://github.com/wcrafton3d/CoA_Codex.git`\
**Application:** Discord fantasy-world Codex/wiki bot\
**Stack:** Python, discord.py, SQLite

## 1. Purpose

This is the durable handoff/context document for the CoA Codex project.
A new ChatGPT Work/Codex session should read this file before making
changes.

Development rules:

-   Inspect current source before editing it.
-   Prefer small, isolated, testable changes.
-   Preserve production data and backwards compatibility.
-   Run syntax/database checks before live Discord tests.
-   Review `git diff` before commits.
-   Back up production SQLite before migrations/significant deployments.
-   Never commit credentials, `.env`, databases, SSH keys, or tokens.
-   Never run the local and OCI Discord bots simultaneously when they
    share the same token.

## 2. Collaboration style

Development has proceeded in explicit checkpoints. The preferred
workflow is cautious and step-by-step rather than large batches. Avoid
unrelated refactors during feature work. Explain the intent of a change,
inspect the exact implementation, test it, then proceed.

The user is comfortable with PowerShell, SSH, Git, Python, and SQLite
when commands and checkpoints are explicit.

## 3. Local environment

-   Windows
-   Python 3.10
-   discord.py 2.7.1
-   python-dotenv 1.2.1
-   Repository: `https://github.com/wcrafton3d/CoA_Codex.git`

Important repository files include `bot.py`, `.gitignore`,
`.env.example`, `requirements.txt`, and this `PROJECT_CONTEXT.md`.

Local/runtime files must not be committed, including `.env`, `wiki.db`,
database backups, credentials, and tokens.

Historically ignored local files include:

-   `wiki-pre-category-migration.db`
-   `CoA_Banner.png`
-   `CoA_Logo.png`
-   `CoA_Logo128x128.ico`
-   `desktop.ini`
-   `github_token.txt`

`github_token.txt` contains credential-like material and must never be
committed.

## 4. Production environment

Production runs on Oracle Cloud Infrastructure Always Free:

-   Ubuntu 24.04 ARM64 (`aarch64`)
-   Ampere A1 Flex VM
-   1 OCPU / 6 GB RAM
-   50 GB boot volume
-   Python 3.12.3 available as `python3`
-   Project path: `/home/ubuntu/CoA_Codex`
-   systemd service: `coa-codex.service`
-   virtualenv Python historically:
    `/home/ubuntu/CoA_Codex/.venv/bin/python`

Useful commands:

``` bash
sudo systemctl status coa-codex.service --no-pager
sudo systemctl stop coa-codex.service
sudo systemctl start coa-codex.service
sudo systemctl restart coa-codex.service
journalctl -u coa-codex.service -n 50 --no-pager
```

### Critical Discord-token rule

Local and production use the same Discord bot identity/token. Never
intentionally run both simultaneously. Doing so previously caused
Discord error `40060 Interaction has already been acknowledged`.

Before local live testing: stop production, confirm it is inactive,
start local. Stop local before restarting production.

## 5. Discord configuration

Configuration is loaded through `.env`. Relevant variables include:

-   `DISCORD_GUILD_IDS`
-   `WORLD_BUILDER_ROLE_IDS`

Worldbuilder roles gate authoring/management operations. Never hardcode
private credentials into source.

## 6. Current slash commands

The bot has included:

-   `/wiki`
-   `/wiki-home`
-   `/wiki-link`
-   `/wiki-search`
-   `/wiki-tag`
-   `/wiki-add`
-   `/wiki-edit`
-   `/wiki-delete`
-   `/wiki-manage`

Phase 3 is expanding category administration through Discord.

## 7. SQLite architecture

Database: `wiki.db`

### wiki_entries

Current logical schema:

``` text
id          TEXT PRIMARY KEY
title       TEXT NOT NULL
category    TEXT NOT NULL
content     TEXT NOT NULL
tags        TEXT
image_url   TEXT
```

### wiki_relationships

Stores links/relationships among Codex entries. Preserve existing
relationship data during migrations.

### wiki_categories

Authoritative category registry foundation:

``` sql
CREATE TABLE IF NOT EXISTS wiki_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT,
    icon TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
)
```

Default seeded categories:

  ------------------------------------------------------------------------
                  Order Category         Icon             Description
  --------------------- ---------------- ---------------- ----------------
                     10 World            🌎               World-level lore
                                                          and foundational
                                                          information.

                     20 Location         📍               Places, regions,
                                                          settlements, and
                                                          geographic
                                                          features.

                     30 NPC              👤               Non-player
                                                          characters and
                                                          notable
                                                          individuals.

                     40 Faction          ⚔️               Organizations,
                                                          factions, and
                                                          political
                                                          groups.

                     50 Bestiary         🐉               Creatures and
                                                          monsters
                                                          encountered in
                                                          the world.
  ------------------------------------------------------------------------

The registry is intended to become authoritative so new categories do
not require Python edits.

## 8. Initialization/migration

The application contains:

-   `initialize_database()`
-   `migrate_database()`
-   `initialize_categories()`

`initialize_categories()` uses `INSERT OR IGNORE`, allowing defaults to
be seeded without overwriting existing category rows.

Migrations must remain safe for existing production data.

## 9. Important tuple/query compatibility rule

Not every wiki query returns the same columns.

Full entry retrieval now includes:

`id, title, category, content, tags, image_url`

Some search/category/tag queries intentionally return fewer columns.
Historical search results may return only three.

**Never globally convert every tuple unpack to six values. Inspect the
exact SELECT first.**

## 10. Phase 1 --- Category Registry Foundation

**Status: COMPLETE and deployed.**

Implemented:

-   `wiki_categories`
-   migration support
-   `initialize_categories()`
-   five default categories including Bestiary
-   descriptions, icons, and `sort_order`

Production verification confirmed `image_url` in `wiki_entries`, all
five registry categories, and at that checkpoint 10 entries / 17
relationships. Those counts are historical, not invariants.

## 11. Phase 2 --- Optional Wiki Images

**Status: COMPLETE, deployed, and production-tested.**

Goal: optional HTTPS image URL on an entry, rendered natively in the
Discord embed without intentionally displaying the raw URL.

Implemented:

-   `image_url TEXT` migration
-   `add_entry(..., image_url=None)`
-   full entry retrieval includes `image_url`
-   `update_entry()` updates `image_url` and returns whether a row was
    affected
-   `WikiEditModal` uses Title, Category, Tags, Image URL, Content
-   validation rejects non-HTTPS URLs
-   display uses:

``` python
if image_url:
    embed.set_image(url=image_url)
```

`/wiki-add` does not currently expose image URL because of Discord modal
input constraints.

Passed tests:

-   set/replace image
-   image renders
-   edit prepopulates URL
-   clear URL removes image
-   `http://` rejected
-   survives restart
-   production verified

Postimg was intermittently unreliable for Discord rendering; other hosts
worked. Treat host failures separately from bot logic where appropriate.

## 12. Git/deployment checkpoint for Phases 1--2

Important completed commit:

`5483dda Add wiki image support and category database migration`

It was pushed to `main` and deployed.

A production backup was created before that deployment, historically
named:

`wiki-before-phase2-20260903-012945.db`

Always make a fresh backup before future migration work.

## 13. Phase 3 --- Category Registry Integration

### Objective

Turn `wiki_categories` into the authoritative Discord-facing category
system.

Desired end state:

-   `/wiki-home` dynamically reads registered categories.
-   Icons/descriptions/order come from SQLite.
-   Empty registered categories still appear.
-   Worldbuilders can add/edit/delete categories through Discord.
-   `/wiki-manage` exposes category management.
-   Entry creation/editing uses registered categories instead of
    unrestricted free-form text.
-   New categories require no source edit or manual SQLite/SSH work.

### Category deletion rule

Do not silently delete a category containing entries. Initial behavior
should refuse deletion until entries are reassigned. A future bulk-move
workflow may be added separately.

### Discord UI constraint

Discord modals cannot contain select/dropdown menus, and modal inputs
are constrained. Registry-controlled category selection may therefore
need to occur outside the entry modal (for example, select category
first, then open the editor).

## 14. Phase 3A --- Dynamic Category Display

**Status: COMPLETE and deployed on 2026-09-08. User accepted the live-test checkpoint; production startup, database, and helper checks passed.**

This is the exact handoff checkpoint as of 2026-09-08.

### Root cause of missing Bestiary

`wiki-home` did not show Bestiary because `get_categories()` still
inferred categories from existing `wiki_entries`. A registered category
with zero entries was therefore invisible.

`get_category_icon()` also still used the old hardcoded icon map.

### Local changes already made

`get_categories()` is now:

``` python
def get_categories():
    """Return all registered Codex categories."""

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT name
        FROM wiki_categories
        ORDER BY sort_order, name COLLATE NOCASE
    """)

    categories = [
        row[0]
        for row in cursor.fetchall()
    ]

    connection.close()

    return categories
```

`get_category_icon()` is now:

``` python
def get_category_icon(category: str) -> str:
    """Return the registered icon for a Codex category."""

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT icon
        FROM wiki_categories
        WHERE name = ? COLLATE NOCASE
    """, (category,))

    row = cursor.fetchone()

    connection.close()

    if row and row[0]:
        return row[0]

    return "📚"
```

The old `CATEGORY_ICONS` dictionary was deliberately left in place
temporarily. Inspection found no references beyond its definition. Removal may be considered separately after Phase 3A; it is not part of this checkpoint.

### Known consumers

`CodexHomeView.__init__()` calls `get_categories()` and uses
`get_category_icon(category)` to build category buttons.

`display_codex_home()` also calls both helpers for the embed category
list.

`display_category()` also uses `get_category_icon()` for its heading.

### Verification and accepted checkpoint

-   Earlier syntax/database checks were recorded as passed. Fresh
    in-memory syntax compilation passed with bundled Python 3.12.14.
    In this Codex environment, `python` was unavailable and `py` found
    no installation; the pinned requirements were installed in a temporary
    dependency directory for testing. Python 3.10 remains the historical
    local runtime, not the interpreter used for this checkpoint.
-   Read-only SQLite integrity and registry checks passed. All five
    categories have the expected icons/order, `image_url` exists, and no
    entry category is unregistered. Local data contained 10 entries and
    15 relationships, including one Bestiary entry; these are checkpoint
    counts, not production invariants.
-   Offline checks using the actual home view and category callbacks
    passed for the category list, dragon icon, required order, and all
    five category browsers, including Bestiary's existing entry.
-   OCI was stopped and confirmed inactive with `MainPID=0` before the
    local bot started. The local bot logged in and registered commands
    successfully using Python 3.12.14 and the pinned requirements.
-   Discord automation could not establish input focus, so it did not
    complete the live command/button checks. The local process was stopped
    and its exit verified before restoring OCI. Production was confirmed
    active and connected to Discord.
-   The user subsequently reported running the live command, manually
    checking the `/wiki-home` button, verifying the Bestiary category
    button, and restarting OCI. The user explicitly accepted Phase 3A
    as complete. The requested test destination was Test Server
    (`1536381888447643699`), `#general`.
-   Detailed icon/order and all-category regression checks above are
    recorded as offline evidence. The user's report explicitly confirms
    the live Bestiary button check.

### Empty-category behavior

A registered category remains listed on home even with zero entries.
Clicking it sends an ephemeral "There are no entries in the ... category"
message and leaves the home view unchanged; it does not open an empty
category browser. The current browser path does not distinguish an empty
registered category from a nonexistent category by registry validation.

This response path passed an offline simulation of an empty query result
for registered Bestiary. No category or entry was created or deleted,
and no database was modified for this simulation. Local Bestiary already
has an entry, so this was not an empty-category live test.

### Deployment checkpoint --- 2026-09-08

-   Feature commit: `f2c71bc60dab421b007c10763546912ed063c897`
    (`Integrate category registry display and record Phase 3A checkpoint`).
    It includes `bot.py` and the newly tracked `PROJECT_CONTEXT.md`, and
    was pushed to GitHub `main` before deployment.
-   A consistent SQLite backup was created using `sqlite3.Connection.backup`
    and passed integrity verification:
    `/home/ubuntu/CoA_Codex/wiki-before-phase3a-20260908-194347.db`.
-   OCI's HTTPS `git pull` could not authenticate noninteractively.
    Deployment therefore used a verified Git bundle of the pushed `main`
    commit, transferred over SSH, fetched on OCI, and merged with
    `--ff-only`. No GitHub credentials were added to the VM. The usual
    HTTPS pull procedure requires authentication to be resolved before
    reuse; the SSH bundle procedure is an available fallback.
-   Production syntax compilation passed; `coa-codex.service` restarted
    successfully and reconnected to the Discord gateway.
-   Read-only post-deployment checks passed: SQLite integrity, `image_url`
    column, expected registry order, and Bestiary's dragon icon. Production
    retained 12 entries and 19 relationships, matching the backup.
-   Production Bestiary has zero entries. After deployment, the user
    completed the visual `/wiki-home` check and confirmed that the Bestiary
    button is present and displays the expected "no entries" response.
    This confirms the empty-category behavior live in production; the
    visual verification was performed manually, not by automation.
-   The production checkout already had untracked `.venv/` and the Phase 2
    backup; the new Phase 3A backup is also untracked and must not be staged.
    Fetching a bundle does not refresh OCI's `origin/main` tracking ref;
    compare the actual `HEAD` with the pushed commit when checking parity.

Phase 3B is implemented locally (see Section 15). Keep unused `CATEGORY_ICONS`
cleanup separate from this deployed feature.

## 15. Proposed Phase 3 roadmap after 3A

### Phase 3B --- Category database helpers

**Status: IMPLEMENTED LOCALLY; 11 database tests passed on 2026-09-13.
Not committed or deployed. Review is the next checkpoint.**

New helpers in `bot.py`:

-   `get_category(category)` returns `(id, name, description, icon,
    sort_order)` or `None` if missing.
-   `category_exists(category)` returns a boolean, including `True` for
    registered categories with no entries.
-   `get_category_entry_count(category)` returns an integer (`0` for an
    empty category), or `None` for an unregistered category.
-   `create_category(name, description=None, icon=None, sort_order=0)`
    returns `success` or `duplicate`.
-   `update_category(category, name, description, icon, sort_order)`
    replaces all fields, returning `success`, `not_found`, or `duplicate`.
    All replacement fields are required; `None` clears description/icon.
    Renames preserve the registry ID and atomically update matching entry
    category strings, including case-only renames. Content, images, tags,
    entry IDs, and relationships are preserved.
-   `delete_category(category)` returns `success`, `not_found`, or `in_use`.
    It refuses deletion if any matching entry exists. A write lock covers
    the usage check and deletion to prevent an intervening write.

Names are trimmed and compared using SQLite `COLLATE NOCASE` (ASCII
case-insensitivity, matching the existing schema). New names must contain
1–50 characters, matching the current entry modal's category limit.
Descriptions/icons accept text or `None`; sort order must be a signed
64-bit integer, not a boolean. Invalid fields raise `ValueError`;
unexpected SQLite errors propagate after rollback and connection cleanup.
Future UI callers must handle those errors and enforce Worldbuilder roles.

The existing display helpers and query tuple shapes are unchanged. No
schema migration or Discord UI wiring was introduced. Category helpers
are not yet called by the management panel or entry authoring flows.

Tests: `python -m unittest discover -s tests -v`, using an available Python
interpreter. This checkpoint used bundled Python 3.12.14. The standard-library
suite in `tests/test_category_registry.py` extracts only named function
definitions from `bot.py`, avoiding its import-time Discord startup and
`.env` access, and runs the real schema initializers on temporary databases.
It covers missing/empty categories, validation, duplicates, ordering/icons,
renames and data preservation, rollback failures, guarded deletion, custom
category deletion persistence, and competing-writer exclusion. All 11 tests
passed; source syntax and diff whitespace checks also passed. Neither bot
was started and no local or production database was changed during Phase 3B.

**Before Phase 3C exposes mutations:** existing startup code still runs
`initialize_categories()` with `INSERT OR IGNORE` on every launch, so
renamed/deleted default category names will be re-created on restart.
Startup also seeds fixed entries with default category strings. Resolve
that startup policy in a separate tested checkpoint before offering default
category rename/delete in Discord. Phase 3E must also validate entry writes
against the registry: the deletion lock prevents a write during the check,
but does not prevent a later free-form entry from reusing a deleted name.

### Phase 3C --- Category management commands/UI

Add Worldbuilder-facing category management, potentially:

-   `/wiki-category-add`
-   `/wiki-category-edit`
-   `/wiki-category-delete`

Category fields:

-   name
-   description
-   icon
-   sort/display order

Prevent case-insensitive duplicates.

### Phase 3D --- `/wiki-manage` integration

Expose category administration through the existing management interface
so Worldbuilders do not need Python or SQLite access.

### Phase 3E --- Registry-controlled entry categories

Replace free-form category assignment with registered-category
selection/validation. Because select menus cannot live inside modals,
likely choose the category before opening the remaining entry editor.

Preserve existing authoring behavior while migrating.

### Phase 3F --- Cleanup

After registry behavior is authoritative and tested:

-   inspect/remove obsolete hardcoded category structures
-   update comments/docstrings
-   regression test
-   update this document

## 16. Navigation considerations

The Codex uses state/history navigation helpers for home, category,
entry, tags, and tag views. Preserve back/navigation behavior when
adding management UI.

`CodexHomeView` dynamically creates category buttons and includes Browse
Tags.

Historically it slices categories with `categories[:20]`. As category
count grows, Discord component limits may require pagination or a select
menu.

## 17. Category browser considerations

`get_category_entries(category)` historically selects:

`id, title, category, content, tags`

Do not add `image_url` or change tuple shape unless consumers need it.

An empty registered category is valid and should be distinguished from a
nonexistent category.

## 18. Production deployment procedure

Before deployment:

1.  local tests pass
2.  review `git diff`
3.  confirm no secrets/databases staged
4.  commit/push
5.  SSH to OCI
6.  inspect production repo status
7.  back up `wiki.db`

Example:

``` bash
cp wiki.db "wiki-before-change-$(date +%Y%m%d-%H%M%S).db"
```

Deploy:

``` bash
git pull origin main
sudo systemctl restart coa-codex.service
sudo systemctl status coa-codex.service --no-pager
journalctl -u coa-codex.service -n 50 --no-pager
```

Verify clean startup, migrations, DB integrity, Discord connection, and
changed behavior.

## 19. Git safety checklist

Before commits:

``` bash
git status
git diff
git diff --cached
```

Never commit:

-   `.env`
-   Discord token
-   GitHub token
-   SSH private key
-   `wiki.db`
-   database backups
-   accidental OS metadata

Keep feature commits focused.

## 20. Standard testing sequence

1.  Inspect current implementation.
2.  Edit locally.
3.  `python -m py_compile .ot.py`
4.  Run targeted SQLite/helper checks.
5.  Inspect `git diff`.
6.  Stop production if local live Discord testing is needed.
7.  Run local bot and test feature/regressions.
8.  Stop local bot.
9.  Commit/push.
10. Back up production DB when appropriate.
11. Deploy.
12. Inspect service/logs.
13. Test production.
14. Update `PROJECT_CONTEXT.md`.

Avoid combining migration, UI redesign, refactoring, and unrelated
cleanup in one unreviewed change.

## 21. Architectural direction

For categories:

**Old:** hardcoded/inferred Python categories → Discord UI

**Target:** `wiki_categories` registry → helpers/validation → Discord UI

This lets Worldbuilders expand the Codex without developer intervention.

Potential future categories discussed as examples include Deities,
Items, Magic, and History. Do not seed them unless requested.

## 22. Category design rules

-   `sort_order` is primary ordering; name is deterministic secondary
    ordering.
-   Names are unique with `COLLATE NOCASE`.
-   Icons come from `wiki_categories.icon`, with `📚` as a reasonable
    fallback.
-   Descriptions should eventually be surfaced where useful.
-   Empty registered categories remain valid.
-   Deletion must not orphan entries.
-   Revisit the one-button-per-category home UI as category count grows.

## 23. Security

Never place secrets in this document.

Sensitive local materials may include `.env`, Discord token, GitHub
token, and SSH private key. They are intentionally omitted.

If a credential is ever committed, rotate it; merely deleting it from
the latest source does not remove it from Git history.

## 24. ChatGPT Project / Codex handoff

The long-running development conversation is intended to live in the
**CoA Codex Development** ChatGPT Project. This file is the
repository-side context bridge.

Recommended opening instruction for a new coding session:

> Read `PROJECT_CONTEXT.md` first, then inspect the current repository
> and Git status. Continue from the documented checkpoint. Do not make
> changes until you reconcile this document with the actual source tree.
> Preserve the small-step testing and deployment workflow.

If repository state conflicts with this document, the
repository/database are the current technical truth; this document
records intent and history. Report discrepancies before changing
behavior.

## 25. Immediate handoff summary

As of 2026-09-13:

-   Phase 1 category registry: **complete/deployed**
-   Phase 2 image support: **complete/deployed/production-tested**
-   Phase 3: **in progress**
-   Phase 3A local code changes: **made**
-   Phase 3A syntax check: **passed**
-   Phase 3A local DB check: **passed**
-   Phase 3A: **complete; user accepted the live Bestiary button check**
-   See Section 14 for live versus offline verification scope and empty-category behavior.
-   Phase 3A changes: **committed, pushed, and deployed; see Section 14**
-   Phase 3B helpers: **implemented locally; 11 tests passed; uncommitted,
    not deployed**
-   `PROJECT_CONTEXT.md`: **tracked; deployment checkpoint recorded**
-   Local bot: **stopped**; OCI: **active and connected after Phase 3A deployment**.
-   Treat OCI as the live bot until explicitly stopped.
-   Do not start local while production is live.

**Next development action:** review the Phase 3B code/tests and context
changes before commit or deployment. Resolve default-category startup
seeding before exposing rename/delete in Phase 3C. Phase 3A production
visual verification is complete.

## 26. Maintenance rule

Update this file whenever:

-   a phase completes
-   schema changes
-   deployment architecture changes
-   a major design decision changes
-   commands/workflows are added
-   limitations are introduced/resolved
-   the handoff checkpoint changes

Keep it readable at session start but detailed enough that a new
development agent does not need to guess at architectural intent.
