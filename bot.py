import os
import sqlite3

import discord
from discord import app_commands
from dotenv import load_dotenv


# --------------------------------------------------
# Configuration
# --------------------------------------------------

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

DATABASE = "wiki.db"


# --------------------------------------------------
# Discord Servers
# --------------------------------------------------

DISCORD_GUILD_IDS = [
    int(guild_id.strip())
    for guild_id in os.getenv(
        "DISCORD_GUILD_IDS",
        ""
    ).split(",")
    if guild_id.strip()
]


# --------------------------------------------------
# Worldbuilder Roles
# --------------------------------------------------

WORLD_BUILDER_ROLE_IDS = {}

for mapping in os.getenv(
    "WORLD_BUILDER_ROLE_IDS",
    ""
).split(","):

    if not mapping.strip():
        continue

    guild_id, role_id = mapping.split(":")

    WORLD_BUILDER_ROLE_IDS[
        int(guild_id.strip())
    ] = int(role_id.strip())

# --------------------------------------------------
# Relationship Types
# --------------------------------------------------

RELATIONSHIP_TYPES = {
    "contains": "contained_by",
    "located_in": "contains",
    "member_of": "has_member",
    "rules": "ruled_by",
    "created_by": "created",
    "allied_with": "allied_with",
    "enemy_of": "enemy_of",
    "related_to": "related_to",
    "associated_with": "associated_with",
    "resides_in": "contains"
}

intents = discord.Intents.default()
intents.members = True


# --------------------------------------------------
# Database
# --------------------------------------------------

def initialize_database():
    """Create tables and record whether this database needs first-run content.

    A pre-existing entries table means an existing installation, even if
    empty: never infer first-run status from row counts or restore deletions.
    """
    connection = sqlite3.connect(DATABASE)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.cursor()
            existing = cursor.execute("""
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'wiki_entries'
            """).fetchone() is not None
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wiki_entries (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wiki_relationships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    FOREIGN KEY (source_id)
                        REFERENCES wiki_entries(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (target_id)
                        REFERENCES wiki_entries(id)
                        ON DELETE CASCADE,
                    UNIQUE(source_id, relationship, target_id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wiki_bootstrap (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    state TEXT NOT NULL CHECK (state IN ('pending', 'complete'))
                )
            """)
            cursor.execute("""
                INSERT OR IGNORE INTO wiki_bootstrap (id, state) VALUES (1, ?)
            """, ('complete' if existing else 'pending',))
    finally:
        connection.close()


def add_entry(
    entry_id,
    title,
    category,
    content,
    tags="",
    image_url=None
):
    """Create an entry in a registered category.

    Return ``success``, ``duplicate``, or ``invalid_category``. The category
    is validated under the write lock and stored using its registry spelling.
    """
    connection = sqlite3.connect(DATABASE)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""
                SELECT name FROM wiki_categories
                WHERE name = ? COLLATE NOCASE
            """, (category.strip(),)).fetchone()
            if row is None:
                return "invalid_category"

            cursor = connection.execute("""
                INSERT OR IGNORE INTO wiki_entries
                (id, title, category, content, tags, image_url)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                entry_id,
                title,
                row[0],
                content,
                tags,
                image_url
            ))
            if not cursor.rowcount:
                return "duplicate"
            connection.execute("""
                INSERT INTO wiki_entry_pages (entry_id, page_number, content)
                VALUES (?, 1, ?)
            """, (entry_id, content))
            return "success"
    finally:
        connection.close()


def get_entry(entry_id):
    """Retrieve a wiki entry by its ID."""

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, title, category, content, tags, image_url
        FROM wiki_entries
        WHERE id = ?
    """, (entry_id.lower(),))

    entry = cursor.fetchone()

    connection.close()

    return entry


def _validate_entry_page_content(content: str) -> str:
    """Validate content accepted by Discord's entry-page authoring flow."""

    if not isinstance(content, str):
        raise ValueError("Page content must be text.")
    if not content.strip():
        raise ValueError("Page content cannot be empty.")
    if len(content) > 4000:
        raise ValueError("Page content cannot exceed 4,000 characters.")
    return content


def _validate_entry_page_id(page_id: int) -> int:
    """Return a valid stable page identifier."""

    if isinstance(page_id, bool) or not isinstance(page_id, int) or page_id < 1:
        raise ValueError("Page ID must be a positive integer.")
    return page_id


def _sync_entry_page_one(connection, entry_id: str):
    """Mirror page 1 into wiki_entries.content during the Phase 4 transition."""

    row = connection.execute("""
        SELECT content FROM wiki_entry_pages
        WHERE entry_id = ? AND page_number = 1
    """, (entry_id,)).fetchone()
    if row is not None:
        connection.execute("""
            UPDATE wiki_entries SET content = ? WHERE id = ?
        """, (row[0], entry_id))


def get_entry_pages(entry_id: str):
    """Return an entry's pages as ``(id, page_number, content)`` tuples."""

    connection = sqlite3.connect(DATABASE)
    try:
        return connection.execute("""
            SELECT id, page_number, content
            FROM wiki_entry_pages
            WHERE entry_id = ?
            ORDER BY page_number
        """, (entry_id.lower(),)).fetchall()
    finally:
        connection.close()


def get_entry_page(entry_id: str, page_number: int = 1):
    """Return one ordered page tuple, or ``None`` when it does not exist."""

    if isinstance(page_number, bool) or not isinstance(page_number, int):
        raise ValueError("Page number must be a positive integer.")
    if page_number < 1:
        raise ValueError("Page number must be a positive integer.")
    connection = sqlite3.connect(DATABASE)
    try:
        return connection.execute("""
            SELECT id, page_number, content
            FROM wiki_entry_pages
            WHERE entry_id = ? AND page_number = ?
        """, (entry_id.lower(), page_number)).fetchone()
    finally:
        connection.close()


def get_entry_page_count(entry_id: str):
    """Return an entry's page count, or ``None`` when the entry is missing."""

    connection = sqlite3.connect(DATABASE)
    try:
        row = connection.execute("""
            SELECT COUNT(p.id)
            FROM wiki_entries AS e
            LEFT JOIN wiki_entry_pages AS p ON p.entry_id = e.id
            WHERE e.id = ?
            GROUP BY e.id
        """, (entry_id.lower(),)).fetchone()
        return row[0] if row is not None else None
    finally:
        connection.close()


def add_entry_page(entry_id: str, content: str) -> str:
    """Append a page, returning ``success`` or ``not_found``."""

    content = _validate_entry_page_content(content)
    entry_id = entry_id.lower()
    connection = sqlite3.connect(DATABASE)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM wiki_entries WHERE id = ?",
                (entry_id,)
            ).fetchone() is None:
                return "not_found"
            page_number = connection.execute("""
                SELECT COALESCE(MAX(page_number), 0) + 1
                FROM wiki_entry_pages WHERE entry_id = ?
            """, (entry_id,)).fetchone()[0]
            connection.execute("""
                INSERT INTO wiki_entry_pages (entry_id, page_number, content)
                VALUES (?, ?, ?)
            """, (entry_id, page_number, content))
            if page_number == 1:
                _sync_entry_page_one(connection, entry_id)
            return "success"
    finally:
        connection.close()


def update_entry_page(entry_id: str, page_id: int, content: str) -> str:
    """Update a stable page ID, returning ``success`` or ``not_found``."""

    page_id = _validate_entry_page_id(page_id)
    content = _validate_entry_page_content(content)
    entry_id = entry_id.lower()
    connection = sqlite3.connect(DATABASE)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""
                SELECT page_number FROM wiki_entry_pages
                WHERE id = ? AND entry_id = ?
            """, (page_id, entry_id)).fetchone()
            if row is None:
                return "not_found"
            connection.execute(
                "UPDATE wiki_entry_pages SET content = ? WHERE id = ?",
                (content, page_id)
            )
            if row[0] == 1:
                _sync_entry_page_one(connection, entry_id)
            return "success"
    finally:
        connection.close()


def delete_entry_page(entry_id: str, page_id: int) -> str:
    """Delete a page and close its ordering gap without deleting the last page."""

    page_id = _validate_entry_page_id(page_id)
    entry_id = entry_id.lower()
    connection = sqlite3.connect(DATABASE)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""
                SELECT page_number FROM wiki_entry_pages
                WHERE id = ? AND entry_id = ?
            """, (page_id, entry_id)).fetchone()
            if row is None:
                return "not_found"
            page_count = connection.execute("""
                SELECT COUNT(*) FROM wiki_entry_pages WHERE entry_id = ?
            """, (entry_id,)).fetchone()[0]
            if page_count <= 1:
                return "only_page"
            deleted_number = row[0]
            connection.execute(
                "DELETE FROM wiki_entry_pages WHERE id = ?",
                (page_id,)
            )
            offset = page_count + 1
            connection.execute("""
                UPDATE wiki_entry_pages
                SET page_number = page_number + ?
                WHERE entry_id = ? AND page_number > ?
            """, (offset, entry_id, deleted_number))
            connection.execute("""
                UPDATE wiki_entry_pages
                SET page_number = page_number - ? - 1
                WHERE entry_id = ? AND page_number > ?
            """, (offset, entry_id, offset + deleted_number))
            _sync_entry_page_one(connection, entry_id)
            return "success"
    finally:
        connection.close()


def move_entry_page(entry_id: str, page_id: int, direction: int) -> str:
    """Move a page one position, preserving its stable ID."""

    page_id = _validate_entry_page_id(page_id)
    if isinstance(direction, bool) or direction not in (-1, 1):
        raise ValueError("Page direction must be -1 or 1.")
    entry_id = entry_id.lower()
    connection = sqlite3.connect(DATABASE)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""
                SELECT page_number FROM wiki_entry_pages
                WHERE id = ? AND entry_id = ?
            """, (page_id, entry_id)).fetchone()
            if row is None:
                return "not_found"
            page_number = row[0]
            target_number = page_number + direction
            target = connection.execute("""
                SELECT id FROM wiki_entry_pages
                WHERE entry_id = ? AND page_number = ?
            """, (entry_id, target_number)).fetchone()
            if target is None:
                return "at_boundary"
            temporary_number = connection.execute("""
                SELECT MAX(page_number) + 1
                FROM wiki_entry_pages WHERE entry_id = ?
            """, (entry_id,)).fetchone()[0]
            connection.execute(
                "UPDATE wiki_entry_pages SET page_number = ? WHERE id = ?",
                (temporary_number, page_id)
            )
            connection.execute(
                "UPDATE wiki_entry_pages SET page_number = ? WHERE id = ?",
                (page_number, target[0])
            )
            connection.execute(
                "UPDATE wiki_entry_pages SET page_number = ? WHERE id = ?",
                (target_number, page_id)
            )
            _sync_entry_page_one(connection, entry_id)
            return "success"
    finally:
        connection.close()


def migrate_database():
    """Apply incremental database schema updates safely."""
    connection = sqlite3.connect(DATABASE)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.cursor()

            cursor.execute("PRAGMA table_info(wiki_entries)")
            columns = {row[1] for row in cursor.fetchall()}
            if "image_url" not in columns:
                cursor.execute("""
                    ALTER TABLE wiki_entries
                    ADD COLUMN image_url TEXT
                """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wiki_categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    description TEXT,
                    icon TEXT,
                    sort_order INTEGER NOT NULL DEFAULT 0
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wiki_entry_pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL CHECK (page_number >= 1),
                    content TEXT NOT NULL,
                    FOREIGN KEY (entry_id)
                        REFERENCES wiki_entries(id)
                        ON DELETE CASCADE,
                    UNIQUE(entry_id, page_number)
                )
            """)
            cursor.execute("""
                INSERT INTO wiki_entry_pages (entry_id, page_number, content)
                SELECT id, 1, content
                FROM wiki_entries
                WHERE TRUE
                ON CONFLICT(entry_id, page_number)
                DO UPDATE SET content = excluded.content
            """)
    finally:
        connection.close()


def initialize_seed_content():
    """Seed categories and starter entries once, only for a new database."""

    categories = [
        (
            "World",
            "World-level lore and foundational information.",
            "🌎",
            10
        ),
        (
            "Location",
            "Places, regions, settlements, and geographic features.",
            "📍",
            20
        ),
        (
            "NPC",
            "Non-player characters and notable individuals.",
            "👤",
            30
        ),
        (
            "Faction",
            "Organizations, factions, and political groups.",
            "⚔️",
            40
        ),
        (
            "Bestiary",
            "Creatures and monsters encountered in the world.",
            "🐉",
            50
        )
    ]

    entries = [
        (
            'setting',
            'The Setting',
            'World',
            'This is the beginning of our tabletop RPG setting. The world is '
            'waiting to be defined.',
            'world, overview, setting',
        ),
        (
            'history',
            'History',
            'World',
            'The history of the world will be documented here.',
            'world, history',
        ),
        (
            'blackwood',
            'Blackwood Forest',
            'Location',
            'Blackwood is an ancient forest whose history and secrets will '
            'eventually be documented here.',
            'location, forest, wilderness',
        ),
        (
            'aldren',
            'Aldren Voss',
            'NPC',
            'Aldren Voss is a character whose history and role in the setting'
            ' will eventually be documented here.',
            'character, npc',
        ),
        (
            'iron-covenant',
            'Iron Covenant',
            'Faction',
            'The Iron Covenant is a faction whose history, goals, and '
            'membership will eventually be documented here.',
            'faction, military',
        ),
    ]

    connection = sqlite3.connect(DATABASE)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state FROM wiki_bootstrap WHERE id = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("Database bootstrap state is missing.")
            if row[0] == 'complete':
                return
            connection.executemany("""
                INSERT OR IGNORE INTO wiki_categories
                (name, description, icon, sort_order) VALUES (?, ?, ?, ?)
            """, categories)
            connection.executemany("""
                INSERT OR IGNORE INTO wiki_entries
                (id, title, category, content, tags) VALUES (?, ?, ?, ?, ?)
            """, entries)
            connection.execute("""
                INSERT OR IGNORE INTO wiki_entry_pages
                (entry_id, page_number, content)
                SELECT id, 1, content FROM wiki_entries
            """)
            connection.execute(
                "UPDATE wiki_bootstrap SET state = 'complete' WHERE id = 1"
            )
    finally:
        connection.close()

# --------------------------------------------------
# Tag Discovery
# --------------------------------------------------

def get_all_tags():
    """Return every unique wiki tag."""

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT tags
        FROM wiki_entries
        WHERE tags IS NOT NULL
          AND TRIM(tags) != ''
    """)

    rows = cursor.fetchall()

    connection.close()

    tags = set()

    for row in rows:

        raw_tags = row[0]

        if not raw_tags:
            continue

        for tag in raw_tags.split(","):

            tag = tag.strip().lower()

            if tag:
                tags.add(tag)

    return sorted(tags)

def get_tag_entries(tag: str):
    """Return wiki entries containing a specific tag."""

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            title,
            category,
            content,
            tags
        FROM wiki_entries
        WHERE tags IS NOT NULL
    """)

    rows = cursor.fetchall()

    connection.close()

    results = []

    search_tag = tag.strip().lower()

    for row in rows:

        raw_tags = row[4] or ""

        entry_tags = [
            item.strip().lower()
            for item in raw_tags.split(",")
            if item.strip()
        ]

        if search_tag in entry_tags:
            results.append(row)

    results.sort(
        key=lambda entry: entry[1].lower()
    )

    return results

# --------------------------------------------------
# Search Wiki
# --------------------------------------------------

def search_wiki(query: str, limit: int = 25):
    """Search wiki entries by title, ID, category, tags, or content."""

    query = query.strip()

    if not query:
        return []

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    search_term = f"%{query}%"

    cursor.execute("""
        SELECT
            id,
            title,
            category,
            content,
            tags
        FROM wiki_entries
        WHERE
            id LIKE ?
            OR title LIKE ?
            OR category LIKE ?
            OR tags LIKE ?
            OR content LIKE ?
        ORDER BY
            CASE
                WHEN id = ? THEN 0
                WHEN title = ? THEN 1
                WHEN title LIKE ? THEN 2
                WHEN id LIKE ? THEN 3
                ELSE 4
            END,
            title COLLATE NOCASE
        LIMIT ?
    """, (
        search_term,
        search_term,
        search_term,
        search_term,
        search_term,
        query.lower(),
        query,
        f"{query}%",
        f"{query}%",
        limit
    ))

    results = cursor.fetchall()

    connection.close()

    return results


def update_entry(
    entry_id,
    title,
    category,
    content,
    tags="",
    image_url=None
):
    """Update an entry using a registered category.

    Return ``success``, ``not_found``, or ``invalid_category``. The category
    is validated under the write lock and stored using its registry spelling.
    """
    connection = sqlite3.connect(DATABASE)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""
                SELECT name FROM wiki_categories
                WHERE name = ? COLLATE NOCASE
            """, (category.strip(),)).fetchone()
            if row is None:
                return "invalid_category"

            cursor = connection.execute("""
                UPDATE wiki_entries
                SET
                    title = ?,
                    category = ?,
                    content = ?,
                    tags = ?,
                    image_url = ?
                WHERE id = ?
            """, (
                title,
                row[0],
                content,
                tags,
                image_url,
                entry_id
            ))
            if not cursor.rowcount:
                return "not_found"
            connection.execute("""
                INSERT INTO wiki_entry_pages (entry_id, page_number, content)
                VALUES (?, 1, ?)
                ON CONFLICT(entry_id, page_number)
                DO UPDATE SET content = excluded.content
            """, (entry_id, content))
            return "success"
    finally:
        connection.close()


def delete_entry(entry_id):
    """Delete an entry and cascade its pages and relationships."""

    connection = sqlite3.connect(DATABASE)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("""
                DELETE FROM wiki_entries
                WHERE id = ?
            """, (entry_id,))
            return cursor.rowcount > 0
    finally:
        connection.close()


# --------------------------------------------------
# Add Relationship
# --------------------------------------------------

def add_relationship(
    source_id: str,
    relationship: str,
    target_id: str
) -> str:

    relationship = relationship.strip().lower()

    # --------------------------------------------------
    # Validate relationship type
    # --------------------------------------------------

    if relationship not in RELATIONSHIP_TYPES:

        return "invalid"

    reverse_relationship = RELATIONSHIP_TYPES[
        relationship
    ]

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    try:

        # --------------------------------------------------
        # Check whether the primary relationship already
        # exists.
        # --------------------------------------------------

        cursor.execute("""
            SELECT 1
            FROM wiki_relationships
            WHERE source_id = ?
              AND relationship = ?
              AND target_id = ?
        """, (
            source_id,
            relationship,
            target_id
        ))

        if cursor.fetchone() is not None:

            connection.close()

            return "duplicate"

        # --------------------------------------------------
        # Check whether the reverse relationship already
        # exists.
        #
        # This protects against an incomplete relationship
        # left behind by an older version of the system.
        # --------------------------------------------------

        cursor.execute("""
            SELECT 1
            FROM wiki_relationships
            WHERE source_id = ?
              AND relationship = ?
              AND target_id = ?
        """, (
            target_id,
            reverse_relationship,
            source_id
        ))

        if cursor.fetchone() is not None:

            connection.close()

            return "duplicate"

        # --------------------------------------------------
        # Create the primary relationship
        # --------------------------------------------------

        cursor.execute("""
            INSERT INTO wiki_relationships
            (source_id, relationship, target_id)
            VALUES (?, ?, ?)
        """, (
            source_id,
            relationship,
            target_id
        ))

        # --------------------------------------------------
        # Create the reverse relationship
        # --------------------------------------------------

        cursor.execute("""
            INSERT INTO wiki_relationships
            (source_id, relationship, target_id)
            VALUES (?, ?, ?)
        """, (
            target_id,
            reverse_relationship,
            source_id
        ))

        connection.commit()

        return "success"

    except sqlite3.IntegrityError:

        connection.rollback()

        return "database_error"

    except sqlite3.Error:

        connection.rollback()

        return "database_error"

    finally:

        connection.close()

def delete_relationship(
    source_id: str,
    relationship: str,
    target_id: str
) -> bool:

    connection = sqlite3.connect(DATABASE)

    cursor = connection.cursor()

    cursor.execute("""
        DELETE FROM wiki_relationships
        WHERE source_id = ?
        AND relationship = ?
        AND target_id = ?
    """, (
        source_id,
        relationship,
        target_id
    ))

    deleted = cursor.rowcount > 0

    connection.commit()

    connection.close()

    return deleted

def get_relationships(
    entry_id: str
):

    connection = sqlite3.connect(DATABASE)

    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            r.relationship,
            e.id,
            e.title,
            e.category
        FROM wiki_relationships r
        JOIN wiki_entries e
            ON r.target_id = e.id
        WHERE r.source_id = ?
        ORDER BY r.relationship, e.title
    """, (entry_id,))

    results = cursor.fetchall()

    connection.close()

    return results

def get_category(category: str):
    """Return (id, name, description, icon, sort_order), or None.

    Names are trimmed and compared using the registry's SQLite NOCASE
    rules (ASCII case-insensitivity).
    """
    connection = sqlite3.connect(DATABASE)
    try:
        return connection.execute("""
            SELECT id, name, description, icon, sort_order
            FROM wiki_categories
            WHERE name = ? COLLATE NOCASE
        """, (category.strip(),)).fetchone()
    finally:
        connection.close()


def category_exists(category: str) -> bool:
    """Whether the trimmed name is registered, including empty categories."""
    return get_category(category) is not None


def get_category_entry_count(category: str):
    """Return an entry count, or None if the category is not registered."""
    connection = sqlite3.connect(DATABASE)
    try:
        row = connection.execute("""
            SELECT COUNT(e.id)
            FROM wiki_categories AS c
            LEFT JOIN wiki_entries AS e
                ON e.category = c.name COLLATE NOCASE
            WHERE c.name = ? COLLATE NOCASE
            GROUP BY c.id
        """, (category.strip(),)).fetchone()
        return row[0] if row is not None else None
    finally:
        connection.close()


def _validate_category_fields(name, description, icon, sort_order):
    """Reject invalid fields before opening a write transaction."""
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 50:
        raise ValueError("Category names must contain 1 to 50 characters.")
    if description is not None and not isinstance(description, str):
        raise ValueError("Category description must be text or None.")
    if icon is not None and not isinstance(icon, str):
        raise ValueError("Category icon must be text or None.")
    if type(sort_order) is not int or not -(2 ** 63) <= sort_order < 2 ** 63:
        raise ValueError("Category sort order must be a SQLite integer.")


def create_category(name, description=None, icon=None, sort_order=0) -> str:
    """Create a category; return 'success' or 'duplicate'.

    Invalid fields raise ValueError; unexpected SQLite errors propagate.
    """
    _validate_category_fields(name, description, icon, sort_order)
    name = name.strip()
    connection = sqlite3.connect(DATABASE)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("""
                SELECT 1 FROM wiki_categories WHERE name = ? COLLATE NOCASE
            """, (name,)).fetchone():
                return "duplicate"
            connection.execute("""
                INSERT INTO wiki_categories (name, description, icon, sort_order)
                VALUES (?, ?, ?, ?)
            """, (name, description, icon, sort_order))
        return "success"
    finally:
        connection.close()


def update_category(category, name, description, icon, sort_order) -> str:
    """Replace all category fields and atomically reassign matching entries.

    Return 'success', 'not_found', or 'duplicate'. All replacement fields
    are required; None clears description/icon. Validation and database
    errors follow create_category(). The category's numeric ID is stable.
    """
    _validate_category_fields(name, description, icon, sort_order)
    name = name.strip()
    connection = sqlite3.connect(DATABASE)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""
                SELECT id, name FROM wiki_categories
                WHERE name = ? COLLATE NOCASE
            """, (category.strip(),)).fetchone()
            if row is None:
                return "not_found"
            category_id, old_name = row
            if connection.execute("""
                SELECT 1 FROM wiki_categories
                WHERE name = ? COLLATE NOCASE AND id != ?
            """, (name, category_id)).fetchone():
                return "duplicate"
            connection.execute("""
                UPDATE wiki_categories
                SET name = ?, description = ?, icon = ?, sort_order = ?
                WHERE id = ?
            """, (name, description, icon, sort_order, category_id))
            connection.execute("""
                UPDATE wiki_entries SET category = ?
                WHERE category = ? COLLATE NOCASE
            """, (name, old_name))
        return "success"
    finally:
        connection.close()


def delete_category(category: str) -> str:
    """Return 'success', 'not_found', or 'in_use'; never delete entries.

    Hold the write lock across the usage check and deletion. This prevents
    another writer from adding entries between those two operations.
    Entry creation and editing use the same write-lock discipline.
    """
    connection = sqlite3.connect(DATABASE)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""
                SELECT id, name FROM wiki_categories
                WHERE name = ? COLLATE NOCASE
            """, (category.strip(),)).fetchone()
            if row is None:
                return "not_found"
            category_id, name = row
            if connection.execute("""
                SELECT 1 FROM wiki_entries
                WHERE category = ? COLLATE NOCASE LIMIT 1
            """, (name,)).fetchone():
                return "in_use"
            connection.execute(
                "DELETE FROM wiki_categories WHERE id = ?", (category_id,)
            )
        return "success"
    finally:
        connection.close()


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

# --------------------------------------------------
# Category Entries
# --------------------------------------------------

def get_category_entries(category: str):
    """Return all wiki entries belonging to a category."""

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, title, category, content, tags
        FROM wiki_entries
        WHERE category = ?
        ORDER BY title COLLATE NOCASE
    """, (category,))

    entries = cursor.fetchall()

    connection.close()

    return entries

# --------------------------------------------------
# Category Pagination
# --------------------------------------------------

CATEGORY_PAGE_SIZE = 20

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

# --------------------------------------------------
# Navigation State
# --------------------------------------------------

def make_home_state():
    """Create a navigation state representing Codex Home."""

    return {
        "type": "home"
    }


def make_category_state(
    category: str,
    page: int = 0
):
    """Create a navigation state for a category page."""

    return {
        "type": "category",
        "category": category,
        "page": page
    }


def make_entry_state(
    entry_id: str
):
    """Create a navigation state for a wiki entry."""

    return {
        "type": "entry",
        "entry_id": entry_id
    }

def make_tags_state(page: int = 0):
    """Create a navigation state for the tag browser."""

    return {
        "type": "tags",
        "page": page
    }


def make_tag_state(tag: str):
    """Create a navigation state for a specific tag."""

    return {
        "type": "tag",
        "tag": tag
    }

# --------------------------------------------------
# Initial Wiki Content
# --------------------------------------------------

initialize_database()
migrate_database()
initialize_seed_content()

# --------------------------------------------------
# Permission Check
# --------------------------------------------------

async def is_worldbuilder(
    interaction: discord.Interaction
) -> bool:

    if interaction.guild is None:
        return False

    role_id = WORLD_BUILDER_ROLE_IDS.get(
        interaction.guild.id
    )

    if role_id is None:
        return False

    member = interaction.guild.get_member(
        interaction.user.id
    )

    if member is None:

        try:

            member = await interaction.guild.fetch_member(
                interaction.user.id
            )

        except discord.NotFound:

            return False

    return any(
        role.id == role_id
        for role in member.roles
    )

async def require_worldbuilder(
    interaction: discord.Interaction
) -> bool:

    if not await is_worldbuilder(interaction):

        await interaction.response.send_message(
            "⛔ You need the **Worldbuilder** role "
            "to modify the RPG Wiki.",
            ephemeral=True
        )

        return False

    return True


# --------------------------------------------------
# Autocomplete
# --------------------------------------------------

async def wiki_autocomplete(
    interaction: discord.Interaction,
    current: str
):

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, title, category
        FROM wiki_entries
        WHERE title LIKE ?
           OR id LIKE ?
        ORDER BY title
        LIMIT 25
    """, (
        f"%{current}%",
        f"%{current}%"
    ))

    entries = cursor.fetchall()

    connection.close()

    return [
        app_commands.Choice(
            name=f"{title} ({category})",
            value=entry_id
        )
        for entry_id, title, category in entries
    ]

async def relationship_autocomplete(
    interaction: discord.Interaction,
    current: str
):

    choices = []

    for relationship in RELATIONSHIP_TYPES:

        if current.lower() in relationship.lower():

            choices.append(
                app_commands.Choice(
                    name=relationship.replace(
                        "_", " "
                    ).title(),
                    value=relationship
                )
            )

    return choices[:25]

# --------------------------------------------------
# Tag Autocomplete
# --------------------------------------------------

async def tag_autocomplete(
    interaction: discord.Interaction,
    current: str
):

    tags = get_all_tags()

    current = current.strip().lower()

    if current:

        tags = [
            tag
            for tag in tags
            if current in tag.lower()
        ]

    tags = tags[:25]

    return [
        app_commands.Choice(
            name=f"#{tag}",
            value=tag
        )
        for tag in tags
    ]


async def category_autocomplete(
    interaction: discord.Interaction,
    current: str
):
    """Return registered category names matching the current text."""
    current = current.strip().lower()
    categories = get_categories()

    if current:
        categories = [
            category
            for category in categories
            if current in category.lower()
        ]

    return [
        app_commands.Choice(
            name=category,
            value=category
        )
        for category in categories[:25]
    ]

# --------------------------------------------------
# Discord Bot
# --------------------------------------------------

class RPGWikiBot(discord.Client):

    def __init__(self):

        super().__init__(intents=intents)

        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):

        # --------------------------------------------------
        # Load configured guilds
        # --------------------------------------------------

        guilds = [
            discord.Object(id=guild_id)
            for guild_id in DISCORD_GUILD_IDS
        ]

        # --------------------------------------------------
        # Save locally registered commands
        # --------------------------------------------------

        commands = self.tree.get_commands()

        print("Commands found locally:")

        for command in commands:
            print(f"  /{command.name}")

        # --------------------------------------------------
        # Clear old GLOBAL commands
        # --------------------------------------------------

        self.tree.clear_commands(guild=None)

        await self.tree.sync()

        print("Global commands cleared.")

        # --------------------------------------------------
        # Register commands in each configured guild
        # --------------------------------------------------

        for guild in guilds:

            # Remove any existing commands for this guild
            self.tree.clear_commands(
                guild=guild
            )

            # Add our locally defined commands
            for command in commands:

                self.tree.add_command(
                    command,
                    guild=guild
                )

            # Synchronize
            guild_commands = await self.tree.sync(
                guild=guild
            )

            print(
                f"Server commands registered "
                f"for guild {guild.id}:"
            )

            for command in guild_commands:

                print(
                    f"  /{command.name}"
                )


bot = RPGWikiBot()


# --------------------------------------------------
# Bot Events
# --------------------------------------------------

@bot.event
async def on_ready():

    print(f"Logged in as {bot.user}")
    print("CoA Codex Bot is online!")
    print(f"Database: {DATABASE}")

    print("Configured servers:")

    for guild_id in DISCORD_GUILD_IDS:

        role_id = WORLD_BUILDER_ROLE_IDS.get(
            guild_id
        )

        print(
            f"  Guild: {guild_id}"
        )

        print(
            f"  Worldbuilder role: {role_id}"
        )

# --------------------------------------------------
# Codex Home View
# --------------------------------------------------

class CodexHomeView(discord.ui.View):

    def __init__(
        self,
        author_id: int
    ):

        super().__init__(timeout=300)

        self.author_id = author_id

        categories = get_categories()

        for category in categories[:20]:

            button = discord.ui.Button(
                label=category[:80],
                emoji=get_category_icon(category),
                style=discord.ButtonStyle.secondary
            )

            button.callback = self.create_category_callback(
                category
            )

            self.add_item(button)

        # --------------------------------------------------
        # Tags Button
        # --------------------------------------------------

        tags_button = discord.ui.Button(
            label="Browse Tags",
            emoji="🏷️",
            style=discord.ButtonStyle.primary,
            row=3
        )

        async def tags_callback(
            interaction: discord.Interaction
        ):

            history = [
                make_home_state()
            ]

            await display_tag_browser(
                interaction,
                page=0,
                history=history
            )

        tags_button.callback = tags_callback

        self.add_item(tags_button)

    def create_category_callback(
        self,
        category: str
    ):

        async def callback(
            interaction: discord.Interaction
        ):

            history = [
                make_home_state()
            ]

            await display_category(
                interaction,
                category,
                page=0,
                history=history
            )

        return callback

# --------------------------------------------------
# Display Codex Home
# --------------------------------------------------

async def display_codex_home(
    interaction: discord.Interaction,
    edit=False,
    history=None
):

    if history is None:
        history = []

    categories = get_categories()

    embed = discord.Embed(
        title="📖 The CoA Codex",
        description=(
            "Welcome to the Codex.\n\n"
            "Explore the world, its people, "
            "factions, locations, history, and more "
            "using the categories below."
        ),
        color=discord.Color.gold()
    )

    if categories:

        category_lines = []

        for category in categories:

            icon = get_category_icon(
                category
            )

            category_lines.append(
                f"{icon} **{category}**"
            )

        embed.add_field(
            name="📚 Categories",
            value="\n".join(
                category_lines
            ),
            inline=False
        )

    embed.add_field(
        name="🔎 Getting Started",
        value=(
            "Use `/wiki` to search for a specific "
            "entry, or select a category below "
            "to browse the Codex."
        ),
        inline=False
    )

    embed.add_field(
        name="🏷️ Tags",
        value=(
            "Explore the Codex by subjects, themes, "
            "and associations."
        ),
        inline=False
    )

    embed.set_footer(
        text=f"Total categories: {len(categories)}"
    )

    view = CodexHomeView(
        author_id=interaction.user.id
    )

    if edit:

        if interaction.response.is_done():

            await interaction.edit_original_response(
                embed=embed,
                view=view
            )

        else:

            await interaction.response.edit_message(
                embed=embed,
                view=view
            )

    else:

        await interaction.response.send_message(
            embed=embed,
            view=view
        )

# --------------------------------------------------
# Category Browser View
# --------------------------------------------------

class CategoryView(discord.ui.View):

    def __init__(
        self,
        category: str,
        page: int,
        author_id: int,
        history=None
    ):

        super().__init__(timeout=300)

        self.category = category
        self.page = page
        self.author_id = author_id
        self.history = history or []

        self.entries = get_category_entries(
            category
        )

        self.total_pages = max(
            1,
            (
                len(self.entries)
                + CATEGORY_PAGE_SIZE
                - 1
            )
            // CATEGORY_PAGE_SIZE
        )

        self.page = max(
            0,
            min(
                self.page,
                self.total_pages - 1
            )
        )

        # --------------------------------------------------
        # Entry Buttons
        # --------------------------------------------------

        start = (
            self.page
            * CATEGORY_PAGE_SIZE
        )

        end = start + CATEGORY_PAGE_SIZE

        page_entries = self.entries[
            start:end
        ]

        for entry in page_entries:

            entry_id = entry[0]
            title = entry[1]

            button = discord.ui.Button(
                label=title[:80],
                style=discord.ButtonStyle.secondary
            )

            button.callback = (
                self.create_entry_callback(
                    entry_id
                )
            )

            self.add_item(button)

        # --------------------------------------------------
        # Previous Page
        # --------------------------------------------------

        previous_button = discord.ui.Button(
            label="Previous",
            emoji="◀️",
            style=discord.ButtonStyle.primary,
            disabled=(self.page == 0),
            row=4
        )

        previous_button.callback = (
            self.previous_page
        )

        self.add_item(previous_button)

        # --------------------------------------------------
        # Page Indicator
        # --------------------------------------------------

        page_button = discord.ui.Button(
            label=(
                f"Page {self.page + 1} "
                f"/ {self.total_pages}"
            ),
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=4
        )

        self.add_item(page_button)

        # --------------------------------------------------
        # Next Page
        # --------------------------------------------------

        next_button = discord.ui.Button(
            label="Next",
            emoji="▶️",
            style=discord.ButtonStyle.primary,
            disabled=(
                self.page >= self.total_pages - 1
            ),
            row=4
        )

        next_button.callback = self.next_page

        self.add_item(next_button)

        # --------------------------------------------------
        # Home
        # --------------------------------------------------

        home_button = discord.ui.Button(
            label="Home",
            emoji="🏠",
            style=discord.ButtonStyle.success,
            row=4
        )

        home_button.callback = self.go_home

        self.add_item(home_button)

        # --------------------------------------------------
        # Back
        # --------------------------------------------------

        if self.history:

            back_button = discord.ui.Button(
                label="Back",
                emoji="↩️",
                style=discord.ButtonStyle.primary,
                row=4
            )

            back_button.callback = self.go_back

            self.add_item(back_button)

    # --------------------------------------------------
    # Entry Selection
    # --------------------------------------------------

    def create_entry_callback(
        self,
        entry_id: str
    ):

        async def callback(
            interaction: discord.Interaction
        ):

            entry = get_entry(
                entry_id
            )

            if entry is None:

                await interaction.response.send_message(
                    "❌ That wiki entry no longer exists.",
                    ephemeral=True
                )

                return

            new_history = self.history + [
                make_category_state(
                    self.category,
                    self.page
                )
            ]

            await display_wiki_entry(
                interaction,
                entry,
                history=new_history
            )

        return callback

    # --------------------------------------------------
    # Previous Page
    # --------------------------------------------------

    async def previous_page(
        self,
        interaction: discord.Interaction
    ):

        if self.page <= 0:

            await interaction.response.send_message(
                "❌ You are already on the first page.",
                ephemeral=True
            )

            return

        await display_category(
            interaction,
            self.category,
            self.page - 1,
            history=self.history
        )

    # --------------------------------------------------
    # Next Page
    # --------------------------------------------------

    async def next_page(
        self,
        interaction: discord.Interaction
    ):

        if self.page >= self.total_pages - 1:

            await interaction.response.send_message(
                "❌ You are already on the last page.",
                ephemeral=True
            )

            return

        await display_category(
            interaction,
            self.category,
            self.page + 1,
            history=self.history
        )

    # --------------------------------------------------
    # Back
    # --------------------------------------------------

    async def go_back(
        self,
        interaction: discord.Interaction
    ):

        if not self.history:

            await interaction.response.send_message(
                "❌ There is nowhere to go back to.",
                ephemeral=True
            )

            return

        previous_history = self.history[:-1]
        previous_state = self.history[-1]

        await navigate_to_state(
            interaction,
            previous_state,
            previous_history
        )

    # --------------------------------------------------
    # Home
    # --------------------------------------------------

    async def go_home(
        self,
        interaction: discord.Interaction
    ):

        await display_codex_home(
            interaction,
            edit=True,
            history=[]
        )

# --------------------------------------------------
# Tag Browser View
# --------------------------------------------------

class TagBrowserView(discord.ui.View):

    def __init__(
        self,
        tags,
        page: int,
        author_id: int,
        history=None
    ):

        super().__init__(
            timeout=300
        )

        self.tags = tags
        self.page = page
        self.author_id = author_id
        self.history = history or []

        self.tags_per_page = 20

        self.total_pages = max(
            1,
            (
                len(self.tags)
                + self.tags_per_page
                - 1
            )
            // self.tags_per_page
        )

        # --------------------------------------------------
        # Current Page
        # --------------------------------------------------

        start = (
            self.page
            * self.tags_per_page
        )

        end = start + self.tags_per_page

        page_tags = self.tags[start:end]

        # --------------------------------------------------
        # Tag Buttons
        # --------------------------------------------------

        for tag in page_tags:

            button = discord.ui.Button(
                label=f"#{tag}"[:80],
                style=discord.ButtonStyle.secondary
            )

            button.callback = (
                self.create_tag_callback(tag)
            )

            self.add_item(button)

        # --------------------------------------------------
        # Previous Page
        # --------------------------------------------------

        previous_button = discord.ui.Button(
            label="Previous",
            emoji="◀️",
            style=discord.ButtonStyle.primary,
            disabled=(self.page <= 0),
            row=4
        )

        previous_button.callback = (
            self.previous_page
        )

        self.add_item(previous_button)

        # --------------------------------------------------
        # Page Indicator
        # --------------------------------------------------

        page_button = discord.ui.Button(
            label=(
                f"Page {self.page + 1} / "
                f"{self.total_pages}"
            ),
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=4
        )

        self.add_item(page_button)

        # --------------------------------------------------
        # Next Page
        # --------------------------------------------------

        next_button = discord.ui.Button(
            label="Next",
            emoji="▶️",
            style=discord.ButtonStyle.primary,
            disabled=(
                self.page >= self.total_pages - 1
            ),
            row=4
        )

        next_button.callback = (
            self.next_page
        )

        self.add_item(next_button)

        # --------------------------------------------------
        # Home
        # --------------------------------------------------

        home_button = discord.ui.Button(
            label="Home",
            emoji="🏠",
            style=discord.ButtonStyle.success,
            row=4
        )

        home_button.callback = (
            self.go_home
        )

        self.add_item(home_button)

    # --------------------------------------------------
    # Tag Selection
    # --------------------------------------------------

    def create_tag_callback(
        self,
        tag: str
    ):

        async def callback(
            interaction: discord.Interaction
        ):

            new_history = self.history + [
                make_tags_state(self.page)
            ]

            await display_tag_results(
                interaction,
                tag,
                history=new_history
            )

        return callback

    # --------------------------------------------------
    # Previous Page
    # --------------------------------------------------

    async def previous_page(
        self,
        interaction: discord.Interaction
    ):

        if self.page <= 0:
            return

        await display_tag_browser(
            interaction,
            page=self.page - 1,
            history=self.history
        )

    # --------------------------------------------------
    # Next Page
    # --------------------------------------------------

    async def next_page(
        self,
        interaction: discord.Interaction
    ):

        if self.page >= self.total_pages - 1:
            return

        await display_tag_browser(
            interaction,
            page=self.page + 1,
            history=self.history
        )

    # --------------------------------------------------
    # Home
    # --------------------------------------------------

    async def go_home(
        self,
        interaction: discord.Interaction
    ):

        await display_codex_home(
            interaction,
            edit=True,
            history=[]
        )

# --------------------------------------------------
# Display Tag Browser
# --------------------------------------------------

async def display_tag_browser(
    interaction: discord.Interaction,
    page: int = 0,
    history=None
):

    if history is None:
        history = []

    tags = get_all_tags()

    tags_per_page = 20

    total_pages = max(
        1,
        (
            len(tags)
            + tags_per_page
            - 1
        )
        // tags_per_page
    )

    # Keep page within valid range

    page = max(
        0,
        min(
            page,
            total_pages - 1
        )
    )

    embed = discord.Embed(
        title="🏷️ Codex Tags",
        description=(
            "Browse the Codex by subjects, themes, "
            "associations, and keywords."
        ),
        color=discord.Color.gold()
    )

    if not tags:

        embed.add_field(
            name="No Tags",
            value=(
                "There are currently no tags "
                "in the Codex."
            ),
            inline=False
        )

    else:

        start = (
            page
            * tags_per_page
        )

        end = start + tags_per_page

        page_tags = tags[start:end]

        tag_lines = [
            f"• **#{tag}**"
            for tag in page_tags
        ]

        embed.add_field(
            name=(
                f"🏷️ Available Tags "
                f"({len(tags)})"
            ),
            value="\n".join(tag_lines),
            inline=False
        )

        embed.set_footer(
            text=(
                f"Page {page + 1} / "
                f"{total_pages} • "
                "Select a tag below."
            )
        )

    view = TagBrowserView(
        tags=tags,
        page=page,
        author_id=interaction.user.id,
        history=history
    )

    await interaction.response.edit_message(
        embed=embed,
        view=view
    )

# --------------------------------------------------
# Tag Results View
# --------------------------------------------------

class TagResultsView(discord.ui.View):

    def __init__(
        self,
        tag: str,
        entries,
        author_id: int,
        history=None
    ):

        super().__init__(
            timeout=300
        )

        self.tag = tag
        self.entries = entries
        self.author_id = author_id
        self.history = history or []

        # --------------------------------------------------
        # Entry Buttons
        # --------------------------------------------------

        for entry in entries[:20]:

            entry_id = entry[0]
            title = entry[1]
            category = entry[2]

            button = discord.ui.Button(
                label=(
                    f"{title} [{category}]"
                )[:80],
                style=discord.ButtonStyle.secondary
            )

            button.callback = (
                self.create_entry_callback(
                    entry_id
                )
            )

            self.add_item(button)

        # --------------------------------------------------
        # Back
        # --------------------------------------------------

        if self.history:

            back_button = discord.ui.Button(
                label="Back",
                emoji="↩️",
                style=discord.ButtonStyle.primary,
                row=4
            )

            back_button.callback = (
                self.go_back
            )

            self.add_item(back_button)

        # --------------------------------------------------
        # Home
        # --------------------------------------------------

        home_button = discord.ui.Button(
            label="Home",
            emoji="🏠",
            style=discord.ButtonStyle.success,
            row=4
        )

        home_button.callback = (
            self.go_home
        )

        self.add_item(home_button)

    # --------------------------------------------------
    # Entry Selection
    # --------------------------------------------------

    def create_entry_callback(
        self,
        entry_id: str
    ):

        async def callback(
            interaction: discord.Interaction
        ):

            entry = get_entry(
                entry_id
            )

            if entry is None:

                await interaction.response.send_message(
                    "❌ That wiki entry no longer exists.",
                    ephemeral=True
                )

                return

            new_history = self.history + [
                {
                    "type": "tag",
                    "tag": self.tag
                }
            ]

            await display_wiki_entry(
                interaction,
                entry,
                history=new_history
            )

        return callback

    # --------------------------------------------------
    # Back
    # --------------------------------------------------

    async def go_back(
        self,
        interaction: discord.Interaction
    ):

        if not self.history:

            await interaction.response.send_message(
                "❌ There is nowhere to go back to.",
                ephemeral=True
            )

            return

        previous_history = self.history[:-1]
        previous_state = self.history[-1]

        await navigate_to_state(
            interaction,
            previous_state,
            previous_history
        )

    # --------------------------------------------------
    # Home
    # --------------------------------------------------

    async def go_home(
        self,
        interaction: discord.Interaction
    ):

        await display_codex_home(
            interaction,
            edit=True,
            history=[]
        )

# --------------------------------------------------
# Display Tag Results
# --------------------------------------------------

async def display_tag_results(
    interaction: discord.Interaction,
    tag: str,
    history=None
):

    if history is None:
        history = []

    entries = get_tag_entries(
        tag
    )

    embed = discord.Embed(
        title=f"🏷️ Tag: {tag}",
        description=(
            f"Entries associated with "
            f"the **{tag}** tag."
        ),
        color=discord.Color.blurple()
    )

    if not entries:

        embed.add_field(
            name="No Results",
            value=(
                "No wiki entries currently "
                "use this tag."
            ),
            inline=False
        )

    else:

        result_lines = []

        for entry in entries:

            title = entry[1]
            category = entry[2]

            result_lines.append(
                f"• **{title}** "
                f"`[{category}]`"
            )

        embed.add_field(
            name=f"📚 Entries ({len(entries)})",
            value="\n".join(
                result_lines[:25]
            ),
            inline=False
        )

    embed.set_footer(
        text=f"Tag: {tag}"
    )

    view = TagResultsView(
        tag=tag,
        entries=entries,
        author_id=interaction.user.id,
        history=history
    )

    await interaction.response.edit_message(
        embed=embed,
        view=view
    )

# --------------------------------------------------
# Search Results View
# --------------------------------------------------

class SearchResultsView(discord.ui.View):

    def __init__(
        self,
        query: str,
        results,
        author_id: int,
        history=None
    ):

        super().__init__(timeout=300)

        self.query = query
        self.results = results
        self.author_id = author_id
        self.history = history or []

        # --------------------------------------------------
        # Result Buttons
        # --------------------------------------------------

        for entry in results[:20]:

            entry_id = entry[0]
            title = entry[1]
            category = entry[2]

            button = discord.ui.Button(
                label=(
                    f"{title} [{category}]"
                )[:80],
                style=discord.ButtonStyle.secondary
            )

            button.callback = (
                self.create_entry_callback(
                    entry_id
                )
            )

            self.add_item(button)

        # --------------------------------------------------
        # Back
        # --------------------------------------------------

        if self.history:

            back_button = discord.ui.Button(
                label="Back",
                emoji="↩️",
                style=discord.ButtonStyle.primary,
                row=4
            )

            back_button.callback = self.go_back

            self.add_item(back_button)

        # --------------------------------------------------
        # Home
        # --------------------------------------------------

        home_button = discord.ui.Button(
            label="Home",
            emoji="🏠",
            style=discord.ButtonStyle.success,
            row=4
        )

        home_button.callback = self.go_home

        self.add_item(home_button)

    # --------------------------------------------------
    # Entry Selection
    # --------------------------------------------------

    def create_entry_callback(
        self,
        entry_id: str
    ):

        async def callback(
            interaction: discord.Interaction
        ):

            entry = get_entry(entry_id)

            if entry is None:

                await interaction.response.send_message(
                    "❌ That wiki entry no longer exists.",
                    ephemeral=True
                )

                return

            new_history = self.history + [
                {
                    "type": "search",
                    "query": self.query
                }
            ]

            await display_wiki_entry(
                interaction,
                entry,
                history=new_history
            )

        return callback

    # --------------------------------------------------
    # Back
    # --------------------------------------------------

    async def go_back(
        self,
        interaction: discord.Interaction
    ):

        if not self.history:

            await interaction.response.send_message(
                "❌ There is nowhere to go back to.",
                ephemeral=True
            )

            return

        previous_history = self.history[:-1]
        previous_state = self.history[-1]

        await navigate_to_state(
            interaction,
            previous_state,
            previous_history
        )

    # --------------------------------------------------
    # Home
    # --------------------------------------------------

    async def go_home(
        self,
        interaction: discord.Interaction
    ):

        await display_codex_home(
            interaction,
            edit=True,
            history=[]
        )

# --------------------------------------------------
# Display Search Results
# --------------------------------------------------

async def display_search_results(
    interaction: discord.Interaction,
    query: str,
    history=None
):

    if history is None:
        history = []

    results = search_wiki(
        query,
        limit=25
    )

    embed = discord.Embed(
        title="🔎 Wiki Search",
        description=(
            f"Search results for:\n"
            f"**{query}**"
        ),
        color=discord.Color.blurple()
    )

    if not results:

        embed.add_field(
            name="No Results",
            value=(
                "I couldn't find any wiki entries "
                "matching that search."
            ),
            inline=False
        )

    else:

        result_lines = []

        for entry in results:

            entry_id = entry[0]
            title = entry[1]
            category = entry[2]

            result_lines.append(
                f"• **{title}** "
                f"`[{category}]`"
            )

        embed.add_field(
            name=f"📚 Results ({len(results)})",
            value="\n".join(result_lines),
            inline=False
        )

    embed.set_footer(
        text=(
            "Searches titles, IDs, categories, "
            "tags, and content."
        )
    )

    view = SearchResultsView(
        query=query,
        results=results,
        author_id=interaction.user.id,
        history=history
    )

    await interaction.response.edit_message(
        embed=embed,
        view=view
    )

# --------------------------------------------------
# Display Category
# --------------------------------------------------

async def display_category(
    interaction: discord.Interaction,
    category: str,
    page: int = 0,
    history=None
):

    if history is None:
        history = []

    entries = get_category_entries(
        category
    )

    if not entries:

        await interaction.response.send_message(
            f"❌ There are no entries in "
            f"the **{category}** category.",
            ephemeral=True
        )

        return

    total_pages = max(
        1,
        (
            len(entries)
            + CATEGORY_PAGE_SIZE
            - 1
        )
        // CATEGORY_PAGE_SIZE
    )

    page = max(
        0,
        min(
            page,
            total_pages - 1
        )
    )

    start = (
        page
        * CATEGORY_PAGE_SIZE
    )

    end = start + CATEGORY_PAGE_SIZE

    page_entries = entries[
        start:end
    ]

    icon = get_category_icon(
        category
    )

    embed = discord.Embed(
        title=f"{icon} {category}",
        description=(
            f"Browse the entries in the "
            f"**{category}** category."
        ),
        color=discord.Color.blurple()
    )

    entry_lines = []

    for entry in page_entries:

        entry_lines.append(
            f"• **{entry[1]}**"
        )

    embed.add_field(
        name="📚 Entries",
        value="\n".join(entry_lines),
        inline=False
    )

    embed.set_footer(
        text=(
            f"{len(entries)} total entries • "
            f"Page {page + 1} / {total_pages}"
        )
    )

    view = CategoryView(
        category=category,
        page=page,
        author_id=interaction.user.id,
        history=history
    )

    await interaction.response.edit_message(
        embed=embed,
        view=view
    )

# --------------------------------------------------
# Wiki Navigation View
# --------------------------------------------------

class WikiEntryView(discord.ui.View):

    def __init__(
        self,
        entry_id: str,
        author_id: int,
        history=None
    ):

        super().__init__(timeout=300)

        self.entry_id = entry_id
        self.author_id = author_id
        self.history = history or []

        # --------------------------------------------------
        # Back
        # --------------------------------------------------

        if self.history:

            back_button = discord.ui.Button(
                label="Back",
                emoji="↩️",
                style=discord.ButtonStyle.primary,
                row=0
            )

            back_button.callback = self.go_back

            self.add_item(back_button)

        # --------------------------------------------------
        # Home
        # --------------------------------------------------

        home_button = discord.ui.Button(
            label="Home",
            emoji="🏠",
            style=discord.ButtonStyle.success,
            row=0
        )

        home_button.callback = self.go_home

        self.add_item(home_button)

        # --------------------------------------------------
        # Related Entries
        # --------------------------------------------------

        relationships = get_relationships(
            entry_id
        )

        for (
            relationship,
            related_id,
            related_title,
            related_category
        ) in relationships[:20]:

            button = discord.ui.Button(
                label=(
                    f"{relationship.replace('_', ' ').title()}: "
                    f"{related_title}"
                )[:80],
                style=discord.ButtonStyle.secondary
            )

            button.callback = self.create_callback(
                related_id
            )

            self.add_item(button)

    # --------------------------------------------------
    # Back
    # --------------------------------------------------

    async def go_back(
        self,
        interaction: discord.Interaction
    ):

        if not self.history:

            await interaction.response.send_message(
                "❌ There is nowhere to go back to.",
                ephemeral=True
            )

            return

        previous_history = self.history[:-1]
        previous_state = self.history[-1]

        await navigate_to_state(
            interaction,
            previous_state,
            previous_history
        )

    # --------------------------------------------------
    # Home
    # --------------------------------------------------

    async def go_home(
        self,
        interaction: discord.Interaction
    ):

        try:

            await interaction.response.defer()

            await display_codex_home(
                interaction,
                edit=True,
                history=self.history
            )

        except discord.NotFound:

            return

    # --------------------------------------------------
    # Related Entry
    # --------------------------------------------------

    def create_callback(
        self,
        related_id: str
    ):

        async def callback(
            interaction: discord.Interaction
        ):

            entry = get_entry(
                related_id
            )

            if entry is None:

                await interaction.response.send_message(
                    "❌ That wiki entry no longer exists.",
                    ephemeral=True
                )

                return

            new_history = self.history + [
                make_entry_state(
                    self.entry_id
                )
            ]

            await display_wiki_entry(
                interaction,
                entry,
                history=new_history
            )

        return callback

# --------------------------------------------------
# Navigation Dispatcher
# --------------------------------------------------

async def navigate_to_state(
    interaction: discord.Interaction,
    state,
    history
):

    state_type = state.get("type")

    # --------------------------------------------------
    # Home
    # --------------------------------------------------

    if state_type == "home":

        await display_codex_home(
            interaction,
            edit=True,
            history=history
        )

        return

    # --------------------------------------------------
    # Category
    # --------------------------------------------------

    if state_type == "category":

        await display_category(
            interaction,
            state["category"],
            state["page"],
            history=history
        )

        return

    # --------------------------------------------------
    # Search
    # --------------------------------------------------

    if state_type == "search":

        await display_search_results(
            interaction,
            state["query"],
            history=history
        )

        return

    # --------------------------------------------------
    # Tags
    # --------------------------------------------------

    if state_type == "tags":

        await display_tag_browser(
            interaction,
            page=state.get("page", 0),
            history=history
        )

        return

    # --------------------------------------------------
    # Tag Results
    # --------------------------------------------------

    if state_type == "tag":

        await display_tag_results(
            interaction,
            state["tag"],
            history=history
        )

        return

    # --------------------------------------------------
    # Wiki Entry
    # --------------------------------------------------

    if state_type == "entry":

        entry = get_entry(
            state["entry_id"]
        )

        if entry is None:

            await interaction.response.send_message(
                "❌ That wiki entry no longer exists.",
                ephemeral=True
            )

            return

        await display_wiki_entry(
            interaction,
            entry,
            history=history
        )

        return

    # --------------------------------------------------
    # Invalid State
    # --------------------------------------------------

    await interaction.response.send_message(
        "❌ The navigation state is invalid.",
        ephemeral=True
    )

    # --------------------------------------------------
    # Tags
    # --------------------------------------------------

    if state_type == "tags":

        await display_tag_browser(
            interaction,
            history=history
        )

        return

    # --------------------------------------------------
    # Tag Results
    # --------------------------------------------------

    if state_type == "tag":

        await display_tag_results(
            interaction,
            state["tag"],
            history=history
        )

        return


# --------------------------------------------------
# Display Wiki Entry
# --------------------------------------------------

async def display_wiki_entry(
    interaction: discord.Interaction,
    entry,
    history=None
):

    if history is None:
        history = []

    entry_id, title, category, content, tags, image_url = entry

    embed = discord.Embed(
        title=title,
        description=content,
        color=discord.Color.blurple()
    )

    if image_url:
        embed.set_image(url=image_url)

    # --------------------------------------------------
    # Category
    # --------------------------------------------------

    embed.add_field(
        name="📚 Category",
        value=category,
        inline=True
    )

    # --------------------------------------------------
    # Tags
    # --------------------------------------------------

    if tags:

        embed.add_field(
            name="🏷️ Tags",
            value=tags,
            inline=True
        )

    # --------------------------------------------------
    # Relationships
    # --------------------------------------------------

    relationships = get_relationships(
        entry_id
    )

    if relationships:

        relationship_lines = []

        for (
            relationship,
            related_id,
            related_title,
            related_category
        ) in relationships:

            relationship_name = (
                relationship
                .replace("_", " ")
                .title()
            )

            relationship_lines.append(
                f"**{relationship_name}:** "
                f"{related_title} "
                f"`[{related_category}]`"
            )

        embed.add_field(
            name="🔗 Related Entries",
            value="\n".join(
                relationship_lines
            ),
            inline=False
        )

    # --------------------------------------------------
    # Footer
    # --------------------------------------------------

    embed.set_footer(
        text=f"Wiki ID: {entry_id}"
    )

    # --------------------------------------------------
    # Navigation
    # --------------------------------------------------

    view = WikiEntryView(
        entry_id=entry_id,
        author_id=interaction.user.id,
        history=history
    )

    await interaction.response.edit_message(
        embed=embed,
        view=view
    )

# --------------------------------------------------
# /wiki
# --------------------------------------------------

@bot.tree.command(
    name="wiki",
    description="View an CoA Codex entry."
)
@app_commands.describe(
    topic="The wiki entry you want to view."
)
@app_commands.autocomplete(
    topic=wiki_autocomplete
)
async def wiki(
    interaction: discord.Interaction,
    topic: str
):

    entry = get_entry(topic)

    if entry is None:

        await interaction.response.send_message(
            f"❌ I couldn't find a wiki entry for "
            f"**{topic}**.",
            ephemeral=True
        )

        return

    entry_id, title, category, content, tags, image_url = entry

    embed = discord.Embed(
        title=title,
        description=content,
        color=discord.Color.blurple()
    )

    if image_url:
        embed.set_image(url=image_url)

    # --------------------------------------------------
    # Category
    # --------------------------------------------------

    embed.add_field(
        name="📚 Category",
        value=category,
        inline=True
    )

    # --------------------------------------------------
    # Tags
    # --------------------------------------------------

    if tags:

        embed.add_field(
            name="🏷️ Tags",
            value=tags,
            inline=True
        )

    # --------------------------------------------------
    # Related Entries
    # --------------------------------------------------

    relationships = get_relationships(entry_id)

    if relationships:

        relationship_lines = []

        for (
            relationship,
            related_id,
            related_title,
            related_category
        ) in relationships:

            relationship_name = (
                relationship
                .replace("_", " ")
                .title()
            )

            relationship_lines.append(
                f"**{relationship_name}:** "
                f"{related_title} "
                f"`[{related_category}]`"
            )

        embed.add_field(
            name="🔗 Related Entries",
            value="\n".join(
                relationship_lines
            ),
            inline=False
        )

    embed.set_footer(
        text=f"Wiki ID: {entry_id}"
    )

    view = WikiEntryView(
        entry_id=entry_id,
        author_id=interaction.user.id,
        history=[]
    )

    await interaction.response.send_message(
        embed=embed,
        view=view
    )

# --------------------------------------------------
# /wiki-home
# --------------------------------------------------

@bot.tree.command(
    name="wiki-home",
    description="Open the CoA Codex home page."
)
async def wiki_home(
    interaction: discord.Interaction
):

    await display_codex_home(
        interaction
    )

# --------------------------------------------------
# /wiki-link
# --------------------------------------------------

@bot.tree.command(
    name="wiki-link",
    description="Create a relationship between two wiki entries."
)
@app_commands.describe(
    source="The source wiki entry.",
    relationship="How the entries are related.",
    target="The target wiki entry."
)
@app_commands.autocomplete(
    source=wiki_autocomplete,
    relationship=relationship_autocomplete,
    target=wiki_autocomplete
)
async def wiki_link(
    interaction: discord.Interaction,
    source: str,
    relationship: str,
    target: str
):

    if not await require_worldbuilder(interaction):
        return

    source_entry = get_entry(source)
    target_entry = get_entry(target)

    if source_entry is None:

        await interaction.response.send_message(
            f"❌ Source entry **{source}** could not be found.",
            ephemeral=True
        )

        return

    if target_entry is None:

        await interaction.response.send_message(
            f"❌ Target entry **{target}** could not be found.",
            ephemeral=True
        )

        return

    source_id = source_entry[0]
    target_id = target_entry[0]

    if source_id == target_id:

        await interaction.response.send_message(
            "❌ An entry cannot be linked to itself.",
            ephemeral=True
        )

        return

    relationship = relationship.strip().lower()

    if relationship not in RELATIONSHIP_TYPES:

        await interaction.response.send_message(
            "❌ That is not a valid relationship type.",
            ephemeral=True
        )

        return

    success = add_relationship(
        source_id,
        relationship,
        target_id
    )

    if not success:

        await interaction.response.send_message(
            "❌ That relationship already exists.",
            ephemeral=True
        )

        return

    reverse_relationship = RELATIONSHIP_TYPES[
        relationship
    ]

    await interaction.response.send_message(
    f"🔗 Relationship created:\n\n"
    f"**{source_entry[1]}** "
    f"`{relationship}` "
    f"**{target_entry[1]}**\n"
    f"**{target_entry[1]}** "
    f"`{reverse_relationship}` "
    f"**{source_entry[1]}**",
    ephemeral=True
    )

# --------------------------------------------------
# /wiki-search
# --------------------------------------------------

@bot.tree.command(
    name="wiki-search",
    description="Search the CoA Codex."
)
@app_commands.describe(
    query="What would you like to search for?"
)
async def wiki_search(
    interaction: discord.Interaction,
    query: str
):

    query = query.strip()

    if not query:

        await interaction.response.send_message(
            "❌ Please enter something to search for.",
            ephemeral=True
        )

        return

    await interaction.response.defer()

    results = search_wiki(
        query,
        limit=25
    )

    embed = discord.Embed(
        title="🔎 Wiki Search",
        description=(
            f"Search results for:\n"
            f"**{query}**"
        ),
        color=discord.Color.blurple()
    )

    if not results:

        embed.add_field(
            name="No Results",
            value=(
                "I couldn't find any wiki entries "
                "matching that search."
            ),
            inline=False
        )

    else:

        result_lines = []

        for entry in results:

            title = entry[1]
            category = entry[2]

            result_lines.append(
                f"• **{title}** "
                f"`[{category}]`"
            )

        embed.add_field(
            name=f"📚 Results ({len(results)})",
            value="\n".join(result_lines),
            inline=False
        )

    embed.set_footer(
        text=(
            "Searches titles, IDs, categories, "
            "tags, and content."
        )
    )

    view = SearchResultsView(
        query=query,
        results=results,
        author_id=interaction.user.id,
        history=[]
    )

    await interaction.followup.send(
        embed=embed,
        view=view
    )

# --------------------------------------------------
# /wiki-tag
# --------------------------------------------------

@bot.tree.command(
    name="wiki-tag",
    description="Browse wiki entries by tag."
)
@app_commands.describe(
    tag="The tag you want to explore."
)
@app_commands.autocomplete(
    tag=tag_autocomplete
)
async def wiki_tag(
    interaction: discord.Interaction,
    tag: str
):

    tag = tag.strip().lower()

    available_tags = get_all_tags()

    if tag not in available_tags:

        await interaction.response.send_message(
            f"❌ The tag **{tag}** does not exist.",
            ephemeral=True
        )

        return

    await interaction.response.send_message(
        "🏷️ Loading tag...",
        ephemeral=True
    )

    # Replace the temporary response with
    # the actual tag results.

    entries = get_tag_entries(tag)

    embed = discord.Embed(
        title=f"🏷️ Tag: {tag}",
        description=(
            f"Entries associated with "
            f"the **{tag}** tag."
        ),
        color=discord.Color.blurple()
    )

    if not entries:

        embed.add_field(
            name="No Results",
            value=(
                "No wiki entries currently "
                "use this tag."
            ),
            inline=False
        )

    else:

        result_lines = []

        for entry in entries:

            title = entry[1]
            category = entry[2]

            result_lines.append(
                f"• **{title}** "
                f"`[{category}]`"
            )

        embed.add_field(
            name=f"📚 Entries ({len(entries)})",
            value="\n".join(
                result_lines[:25]
            ),
            inline=False
        )

    view = TagResultsView(
        tag=tag,
        entries=entries,
        author_id=interaction.user.id,
        history=[]
    )

    await interaction.edit_original_response(
        embed=embed,
        view=view
    )

# --------------------------------------------------
# ADD MODAL
# --------------------------------------------------

class WikiAddModal(discord.ui.Modal):

    def __init__(self, category: str):

        super().__init__(
            title="Create Wiki Entry"
        )

        self.category_value = category

        self.entry_id = discord.ui.TextInput(
            label="ID",
            placeholder="e.g. blackwood-coven",
            required=True,
            max_length=100
        )

        self.title_input = discord.ui.TextInput(
            label="Title",
            placeholder="e.g. Blackwood Coven",
            required=True,
            max_length=100
        )

        self.tags = discord.ui.TextInput(
            label="Tags",
            placeholder="e.g. magic, forest, witchcraft",
            required=False,
            max_length=500
        )

        self.content = discord.ui.TextInput(
            label="Content",
            placeholder="Enter the wiki entry...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000
        )

        self.add_item(self.entry_id)
        self.add_item(self.title_input)
        self.add_item(self.tags)
        self.add_item(self.content)

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        entry_id = self.entry_id.value.strip().lower()
        title = self.title_input.value.strip()
        tags = self.tags.value.strip()

        content = self.content.value.strip()

        result = add_entry(
            entry_id,
            title,
            self.category_value,
            content,
            tags
        )

        if result == "invalid_category":
            await interaction.response.send_message(
                "❌ The selected category no longer exists. Reopen the "
                "entry creator and choose another category.",
                ephemeral=True
            )
            return

        if result == "duplicate":
            await interaction.response.send_message(
                f"❌ An entry with the ID `{entry_id}` already exists.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ Wiki entry **{title}** created.\n\n"
            f"ID: `{entry_id}`\n"
            f"Category: **{self.category_value}**",
            ephemeral=True
        )



# --------------------------------------------------
# /wiki-add
# --------------------------------------------------

@bot.tree.command(
    name="wiki-add",
    description="Create a new CoA Codex entry."
)
async def wiki_add(
    interaction: discord.Interaction
):

    if not await require_worldbuilder(interaction):
        return

    await send_entry_category_picker(
        interaction,
        mode="add"
    )


# --------------------------------------------------
# EDIT MODAL
# --------------------------------------------------

class WikiEditModal(discord.ui.Modal):

    def __init__(self, entry, selected_category: str):

        super().__init__(
            title="Edit Wiki Entry"
        )

        entry_id, title, category, content, tags, image_url = entry

        self.entry_id_value = entry_id
        self.category_value = selected_category

        self.title_input = discord.ui.TextInput(
            label="Title",
            default=title,
            required=True,
            max_length=100
        )

        self.tags = discord.ui.TextInput(
            label="Tags",
            default=tags or "",
            required=False,
            max_length=500
        )

        self.image_url = discord.ui.TextInput(
            label="Image URL (optional)",
            placeholder="https://example.com/image.jpg",
            default=image_url or "",
            required=False,
            max_length=1000
        )

        self.content = discord.ui.TextInput(
            label="Content",
            default=content,
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000
        )

        self.add_item(self.title_input)
        self.add_item(self.tags)
        self.add_item(self.image_url)
        self.add_item(self.content)

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        title = self.title_input.value.strip()
        tags = self.tags.value.strip()
        image_url = self.image_url.value.strip()
        content = self.content.value.strip()

        if image_url and not image_url.lower().startswith("https://"):
            await interaction.response.send_message(
                "The image URL must begin with https://",
                ephemeral=True
            )
            return

        result = update_entry(
            self.entry_id_value,
            title,
            self.category_value,
            content,
            tags,
            image_url
        )

        if result == "invalid_category":
            await interaction.response.send_message(
                "❌ The selected category no longer exists. Reopen the "
                "entry editor and choose another category.",
                ephemeral=True
            )
            return

        if result == "not_found":

            await interaction.response.send_message(
                "❌ The wiki entry could not be updated.",
                ephemeral=True
            )

            return

        await interaction.response.send_message(
            f"✅ Wiki entry **{title}** updated.",
            ephemeral=True
        )


# --------------------------------------------------
# Entry Category Selection
# --------------------------------------------------

class EntryCategorySelectView(discord.ui.View):

    PAGE_SIZE = 25

    def __init__(
        self,
        author_id: int,
        mode: str,
        categories,
        entry=None
    ):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.mode = mode
        self.entry_id = entry[0] if entry is not None else None
        self.current_category = entry[2] if entry is not None else None
        self.categories = list(categories)
        self.page = 0
        self.page_count = (
            len(self.categories) + self.PAGE_SIZE - 1
        ) // self.PAGE_SIZE
        self.build_items()

    def build_items(self):
        self.clear_items()
        start = self.page * self.PAGE_SIZE
        page_categories = self.categories[start:start + self.PAGE_SIZE]

        options = [
            discord.SelectOption(
                label=category[:100],
                value=category,
                default=(
                    self.current_category is not None
                    and category.lower() == self.current_category.lower()
                )
            )
            for category in page_categories
        ]

        select = discord.ui.Select(
            placeholder="Select a registered category...",
            options=options,
            min_values=1,
            max_values=1
        )
        select.callback = self.select_category
        self.add_item(select)

        if self.page_count > 1:
            previous_button = discord.ui.Button(
                label="Previous Categories",
                emoji="◀️",
                style=discord.ButtonStyle.secondary,
                row=1,
                disabled=self.page == 0
            )
            previous_button.callback = self.previous_page
            self.add_item(previous_button)

            next_button = discord.ui.Button(
                label="Next Categories",
                emoji="▶️",
                style=discord.ButtonStyle.secondary,
                row=1,
                disabled=self.page >= self.page_count - 1
            )
            next_button.callback = self.next_page
            self.add_item(next_button)

    def prompt(self):
        action = (
            "create an entry in"
            if self.mode == "add"
            else "assign to the entry"
        )
        message = f"Select the registered category to {action}."
        if self.page_count > 1:
            message += f" Page {self.page + 1} of {self.page_count}."
        return message

    async def previous_page(self, interaction: discord.Interaction):
        self.page -= 1
        self.build_items()
        await interaction.response.edit_message(
            content=self.prompt(),
            view=self
        )

    async def next_page(self, interaction: discord.Interaction):
        self.page += 1
        self.build_items()
        await interaction.response.edit_message(
            content=self.prompt(),
            view=self
        )

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "⛔ This category selection belongs to another "
                "Worldbuilder.",
                ephemeral=True
            )
            return False
        return True

    async def select_category(
        self,
        interaction: discord.Interaction
    ):
        selected = interaction.data["values"][0]
        details = get_category(selected)
        if details is None:
            await interaction.response.send_message(
                "❌ That category no longer exists. Reopen the entry "
                "editor and choose another category.",
                ephemeral=True
            )
            return

        category = details[1]
        if self.mode == "add":
            await interaction.response.send_modal(
                WikiAddModal(category)
            )
            return

        entry = get_entry(self.entry_id)
        if entry is None:
            await interaction.response.send_message(
                "❌ That wiki entry could not be found.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(
            WikiEditModal(entry, category)
        )


async def send_entry_category_picker(
    interaction: discord.Interaction,
    mode: str,
    entry=None
):
    try:
        categories = get_categories()
    except sqlite3.Error:
        await interaction.response.send_message(
            "❌ Categories could not be loaded because of a database "
            "error.",
            ephemeral=True
        )
        return

    if not categories:
        await interaction.response.send_message(
            "❌ No registered categories are available. Create a category "
            "before authoring entries.",
            ephemeral=True
        )
        return

    view = EntryCategorySelectView(
        author_id=interaction.user.id,
        mode=mode,
        categories=categories,
        entry=entry
    )
    await interaction.response.send_message(
        view.prompt(),
        view=view,
        ephemeral=True
    )


# --------------------------------------------------
# /wiki-edit
# --------------------------------------------------

@bot.tree.command(
    name="wiki-edit",
    description="Edit an existing CoA Codex entry."
)
@app_commands.describe(
    topic="The codex entry you want to edit."
)
@app_commands.autocomplete(
    topic=wiki_autocomplete
)
async def wiki_edit(
    interaction: discord.Interaction,
    topic: str
):

    if not await require_worldbuilder(interaction):
        return

    entry = get_entry(topic)

    if entry is None:

        await interaction.response.send_message(
            f"❌ I couldn't find a wiki entry for "
            f"**{topic}**.",
            ephemeral=True
        )

        return

    await send_entry_category_picker(
        interaction,
        mode="edit",
        entry=entry
    )


# --------------------------------------------------
# DELETE CONFIRMATION VIEW
# --------------------------------------------------

class DeleteConfirmationView(discord.ui.View):

    def __init__(
        self,
        author_id: int,
        entry_id: str,
        title: str
    ):

        super().__init__(timeout=60)

        self.author_id = author_id
        self.entry_id = entry_id
        self.title = title

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ) -> bool:

        if interaction.user.id != self.author_id:

            await interaction.response.send_message(
                "⛔ Only the person who initiated "
                "this deletion can confirm it.",
                ephemeral=True
            )

            return False

        return True

    @discord.ui.button(
        label="Delete",
        emoji="🗑️",
        style=discord.ButtonStyle.danger
    )
    async def confirm_delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        success = delete_entry(self.entry_id)

        if not success:

            await interaction.response.edit_message(
                content=(
                    "❌ The wiki entry could not be deleted. "
                    "It may have already been removed."
                ),
                embed=None,
                view=None
            )

            return

        await interaction.response.edit_message(
            content=(
                f"🗑️ Wiki entry **{self.title}** "
                "has been deleted."
            ),
            embed=None,
            view=None
        )

        self.stop()

    @discord.ui.button(
        label="Cancel",
        emoji="✖️",
        style=discord.ButtonStyle.secondary
    )
    async def cancel_delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.edit_message(
            content=(
                f"Deletion of **{self.title}** cancelled."
            ),
            embed=None,
            view=None
        )

        self.stop()

    async def on_timeout(self):

        # The original interaction message will simply
        # expire naturally after the buttons become inactive.
        pass


# --------------------------------------------------
# /wiki-delete
# --------------------------------------------------

@bot.tree.command(
    name="wiki-delete",
    description="Delete an CoA Codex entry."
)
@app_commands.describe(
    topic="The codex entry you want to delete."
)
@app_commands.autocomplete(
    topic=wiki_autocomplete
)
async def wiki_delete(
    interaction: discord.Interaction,
    topic: str
):

    if not await require_worldbuilder(interaction):
        return

    entry = get_entry(topic)

    if entry is None:

        await interaction.response.send_message(
            f"❌ I couldn't find a wiki entry for "
            f"**{topic}**.",
            ephemeral=True
        )

        return

    entry_id, title, category, content, tags, image_url = entry

    embed = discord.Embed(
        title="⚠️ Delete Wiki Entry?",
        description=(
            f"Are you sure you want to permanently delete "
            f"**{title}**?\n\n"
            f"**Category:** {category}\n"
            f"**ID:** `{entry_id}`\n\n"
            "⚠️ **This action cannot be undone.**"
        ),
        color=discord.Color.red()
    )

    view = DeleteConfirmationView(
        author_id=interaction.user.id,
        entry_id=entry_id,
        title=title
    )

    await interaction.response.send_message(
        embed=embed,
        view=view,
        ephemeral=True
    )



# --------------------------------------------------
# Link Relationship Selection
# --------------------------------------------------

class WikiLinkRelationshipView(discord.ui.View):

    def __init__(
        self,
        author_id: int,
        source_id: str
    ):

        super().__init__(timeout=300)

        self.author_id = author_id
        self.source_id = source_id

        relationships = [
            ("located_in", "Located in"),
            ("contains", "Contains"),
            ("member_of", "Member of"),
            ("leader_of", "Leader of"),
            ("enemy_of", "Enemy of"),
            ("ally_of", "Ally of"),
            ("created_by", "Created by"),
            ("serves", "Serves"),
            ("rules", "Rules"),
            ("resides_in", "Resides in"),
            ("associated_with", "Associated with")
        ]

        options = [
            discord.SelectOption(
                label=label,
                value=value
            )
            for value, label in relationships
        ]

        select = discord.ui.Select(
            placeholder="Select a relationship...",
            options=options
        )

        async def relationship_callback(
            interaction: discord.Interaction
        ):

            relationship = select.values[0]

            view = discord.ui.View(
                timeout=300
            )

            search_button = discord.ui.Button(
                label="Search Target Entry",
                emoji="🔎",
                style=discord.ButtonStyle.primary
            )

            async def search_target(
                search_interaction: discord.Interaction
            ):

                modal = WikiLinkTargetSearchModal(
                    author_id=self.author_id,
                    source_id=self.source_id,
                    relationship=relationship
                )

                await search_interaction.response.send_modal(
                    modal
                )

            search_button.callback = search_target

            view.add_item(
                search_button
            )

            await interaction.response.send_message(
                f"🔗 Relationship: "
                f"**{relationship.replace('_', ' ').title()}**\n\n"
                "Search for the target entry you want to link.",
                view=view,
                ephemeral=True
            )

        select.callback = relationship_callback

        self.add_item(select)

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ) -> bool:

        if interaction.user.id != self.author_id:

            await interaction.response.send_message(
                "⛔ This management panel belongs "
                "to another Worldbuilder.",
                ephemeral=True
            )

            return False

        return True

# --------------------------------------------------
# Link Target Search Modal
# --------------------------------------------------

class WikiLinkTargetSearchModal(discord.ui.Modal):

    def __init__(
        self,
        author_id: int,
        source_id: str,
        relationship: str
    ):

        super().__init__(
            title="Find Target Entry"
        )

        self.author_id = author_id
        self.source_id = source_id
        self.relationship = relationship

        self.search = discord.ui.TextInput(
            label="Search",
            placeholder="Enter title, ID, category, tag, or content...",
            required=True,
            max_length=100
        )

        self.add_item(self.search)

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        search_text = self.search.value.strip()

        if not search_text:

            await interaction.response.send_message(
                "❌ Please enter a search term.",
                ephemeral=True
            )

            return

        entries = search_wiki(
            search_text,
            limit=25
        )

        # --------------------------------------------------
        # No results
        # --------------------------------------------------

        if not entries:

            await interaction.response.send_message(
                f"❌ No wiki entries matched "
                f"**{search_text}**.",
                ephemeral=True
            )

            return

        # --------------------------------------------------
        # Convert full search results into the format
        # expected by WikiLinkTargetResultsView.
        # --------------------------------------------------

        results = [
            (
                entry[0],
                entry[1],
                entry[2]
            )
            for entry in entries
        ]

        view = WikiLinkTargetResultsView(
            author_id=self.author_id,
            source_id=self.source_id,
            relationship=self.relationship,
            results=results
        )

        await interaction.response.send_message(
            f"🔎 **Target search results for:** "
            f"`{search_text}`\n\n"
            "Select the entry you want to link.",
            view=view,
            ephemeral=True
        )


# --------------------------------------------------
# Link Target Results
# --------------------------------------------------

class WikiLinkTargetResultsView(
    discord.ui.View
):

    def __init__(
        self,
        author_id: int,
        source_id: str,
        relationship: str,
        entries
    ):

        super().__init__(
            timeout=300
        )

        self.author_id = author_id
        self.source_id = source_id
        self.relationship = relationship

        options = []

        for (
            entry_id,
            title,
            category
        ) in entries:

            options.append(
                discord.SelectOption(
                    label=title[:100],
                    value=entry_id,
                    description=(
                        f"{category} • {entry_id}"
                    )[:100]
                )
            )

        select = discord.ui.Select(
            placeholder="Select a target entry...",
            options=options,
            min_values=1,
            max_values=1
        )

        select.callback = self.select_target

        self.add_item(select)

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ) -> bool:

        if interaction.user.id != self.author_id:

            await interaction.response.send_message(
                "⛔ This management panel belongs "
                "to another Worldbuilder.",
                ephemeral=True
            )

            return False

        return True

    async def select_target(
        self,
        interaction: discord.Interaction
    ):

        target_id = interaction.data["values"][0]

        # --------------------------------------------------
        # Prevent self-linking
        # --------------------------------------------------

        if self.source_id == target_id:

            await interaction.response.send_message(
                "❌ An entry cannot be linked to itself.",
                ephemeral=True
            )

            return

        # --------------------------------------------------
        # Retrieve entries
        # --------------------------------------------------

        source_entry = get_entry(
            self.source_id
        )

        target_entry = get_entry(
            target_id
        )

        if (
            source_entry is None
            or target_entry is None
        ):

            await interaction.response.send_message(
                "❌ One of the selected entries "
                "could not be found.",
                ephemeral=True
            )

            return

        # --------------------------------------------------
        # Create relationship
        # --------------------------------------------------

        result = add_relationship(
            self.source_id,
            self.relationship,
            target_id
        )

        # --------------------------------------------------
        # Invalid relationship
        # --------------------------------------------------

        if result == "invalid":

            await interaction.response.send_message(
                "❌ That relationship type "
                "is not recognized.",
                ephemeral=True
            )

            return

        # --------------------------------------------------
        # Duplicate relationship
        # --------------------------------------------------

        if result == "duplicate":

            await interaction.response.send_message(
                "❌ That relationship already exists.",
                ephemeral=True
            )

            return

        # --------------------------------------------------
        # Database error
        # --------------------------------------------------

        if result == "database_error":

            await interaction.response.send_message(
                "❌ The relationship could not be created "
                "because of a database error.",
                ephemeral=True
            )

            return

        # --------------------------------------------------
        # Success
        # --------------------------------------------------

        await interaction.response.send_message(
            f"🔗 **{source_entry[1]}** "
            f"`{self.relationship}` "
            f"**{target_entry[1]}**\n\n"
            "✅ Relationship created.",
            ephemeral=True
        )

        self.stop()


# --------------------------------------------------
# Link Target Results
# --------------------------------------------------

class WikiLinkTargetResultsView(
    discord.ui.View
):

    def __init__(
        self,
        author_id: int,
        source_id: str,
        relationship: str,
        results
    ):

        super().__init__(
            timeout=300
        )

        self.author_id = author_id
        self.source_id = source_id
        self.relationship = relationship
        self.results = results

        for entry_id, title, category in results[:25]:

            button = discord.ui.Button(
                label=(
                    f"{title} ({category})"
                )[:80],
                style=discord.ButtonStyle.secondary
            )

            button.callback = (
                self.create_target_callback(
                    entry_id
                )
            )

            self.add_item(button)

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ) -> bool:

        if interaction.user.id != self.author_id:

            await interaction.response.send_message(
                "⛔ This management panel belongs "
                "to another Worldbuilder.",
                ephemeral=True
            )

            return False

        return True

    def create_target_callback(
        self,
        target_id: str
    ):

        async def callback(
            interaction: discord.Interaction
        ):

            # --------------------------------------------------
            # Prevent self-linking
            # --------------------------------------------------

            if self.source_id == target_id:

                await interaction.response.send_message(
                    "❌ An entry cannot be linked to itself.",
                    ephemeral=True
                )

                return

            # --------------------------------------------------
            # Retrieve entries
            # --------------------------------------------------

            source_entry = get_entry(
                self.source_id
            )

            target_entry = get_entry(
                target_id
            )

            if (
                source_entry is None
                or target_entry is None
            ):

                await interaction.response.send_message(
                    "❌ One of the selected entries "
                    "could not be found.",
                    ephemeral=True
                )

                return

            # --------------------------------------------------
            # Create relationship
            # --------------------------------------------------

            result = add_relationship(
                self.source_id,
                self.relationship,
                target_id
            )

            # --------------------------------------------------
            # Invalid relationship
            # --------------------------------------------------

            if result == "invalid":

                await interaction.response.send_message(
                    "❌ That relationship type "
                    "is not recognized.",
                    ephemeral=True
                )

                return

            # --------------------------------------------------
            # Duplicate relationship
            # --------------------------------------------------

            if result == "duplicate":

                await interaction.response.send_message(
                    "❌ That relationship already exists.",
                    ephemeral=True
                )

                return

            # --------------------------------------------------
            # Database error
            # --------------------------------------------------

            if result == "database_error":

                await interaction.response.send_message(
                    "❌ The relationship could not be "
                    "created because of a database error.",
                    ephemeral=True
                )

                return

            # --------------------------------------------------
            # Success
            # --------------------------------------------------

            await interaction.response.send_message(
                f"🔗 **{source_entry[1]}** "
                f"`{self.relationship}` "
                f"**{target_entry[1]}**\n\n"
                "✅ Relationship created.",
                ephemeral=True
            )

            self.stop()

        return callback

# --------------------------------------------------
# Wiki Entry Search Modal
# --------------------------------------------------

class WikiEntrySearchModal(discord.ui.Modal):

    def __init__(
        self,
        mode: str,
        author_id: int
    ):

        super().__init__(
            title="Find Wiki Entry"
        )

        self.mode = mode
        self.author_id = author_id

        self.search = discord.ui.TextInput(
            label="Search",
            placeholder="Enter part of the title or ID...",
            required=True,
            max_length=100
        )

        self.add_item(self.search)

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        search_text = self.search.value.strip()

        if not search_text:

            await interaction.response.send_message(
                "❌ Please enter a search term.",
                ephemeral=True
            )

            return

        connection = sqlite3.connect(DATABASE)
        cursor = connection.cursor()

        wildcard = f"%{search_text}%"

        cursor.execute("""
            SELECT id, title, category
            FROM wiki_entries
            WHERE title LIKE ?
               OR id LIKE ?
               OR category LIKE ?
               OR tags LIKE ?
            ORDER BY title
            LIMIT 25
        """, (
            wildcard,
            wildcard,
            wildcard,
            wildcard
        ))

        entries = cursor.fetchall()

        connection.close()

        if not entries:

            await interaction.response.send_message(
                f"❌ No wiki entries matched "
                f"**{search_text}**.",
                ephemeral=True
            )

            return

        view = WikiSearchResultView(
            author_id=self.author_id,
            mode=self.mode,
            entries=entries
        )

        await interaction.response.send_message(
            f"🔎 **Search results for:** `{search_text}`\n\n"
            "Select an entry below.",
            view=view,
            ephemeral=True
        )

# --------------------------------------------------
# Wiki Search Result View
# --------------------------------------------------

class WikiSearchResultView(discord.ui.View):

    def __init__(
        self,
        author_id: int,
        mode: str,
        entries
    ):

        super().__init__(
            timeout=300
        )

        self.author_id = author_id
        self.mode = mode

        options = []

        for (
            entry_id,
            title,
            category
        ) in entries:

            options.append(
                discord.SelectOption(
                    label=title[:100],
                    value=entry_id,
                    description=(
                        f"{category} • {entry_id}"
                    )[:100]
                )
            )

        select = discord.ui.Select(
            placeholder="Select a wiki entry...",
            options=options,
            min_values=1,
            max_values=1
        )

        select.callback = self.select_entry

        self.add_item(select)

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ) -> bool:

        if interaction.user.id != self.author_id:

            await interaction.response.send_message(
                "⛔ This search belongs to "
                "another Worldbuilder.",
                ephemeral=True
            )

            return False

        return True

    async def select_entry(
        self,
        interaction: discord.Interaction
    ):

        entry_id = interaction.data["values"][0]

        entry = get_entry(entry_id)

        if entry is None:

            await interaction.response.send_message(
                "❌ That wiki entry could not be found.",
                ephemeral=True
            )

            return

        # --------------------------------------------------
        # Edit
        # --------------------------------------------------

        if self.mode == "edit":

            await send_entry_category_picker(
                interaction,
                mode="edit",
                entry=entry
            )

            return

        # --------------------------------------------------
        # Delete
        # --------------------------------------------------

        if self.mode == "delete":

            entry_id, title, category, content, tags, image_url = entry

            embed = discord.Embed(
                title="⚠️ Delete Wiki Entry?",
                description=(
                    f"Are you sure you want to permanently "
                    f"delete **{title}**?\n\n"
                    f"**Category:** {category}\n"
                    f"**ID:** `{entry_id}`\n\n"
                    "⚠️ **This action cannot be undone.**"
                ),
                color=discord.Color.red()
            )

            view = DeleteConfirmationView(
                author_id=interaction.user.id,
                entry_id=entry_id,
                title=title
            )

            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True
            )

            return

        # --------------------------------------------------
        # Link Source
        # --------------------------------------------------

        if self.mode == "link-source":

            view = WikiLinkRelationshipView(
                author_id=interaction.user.id,
                source_id=entry_id
            )

            await interaction.response.send_message(
                f"🔗 **Source:** {entry[1]}\n\n"
                "Now choose the relationship.",
                view=view,
                ephemeral=True
            )

            return

# --------------------------------------------------
# CATEGORY MANAGEMENT
# --------------------------------------------------

def parse_category_sort_order(value: str) -> int:
    """Parse a modal sort-order value using the helper's integer rules."""
    try:
        sort_order = int(value.strip())
    except ValueError as error:
        raise ValueError("Sort order must be a whole number.") from error

    if not -(2 ** 63) <= sort_order < 2 ** 63:
        raise ValueError("Sort order is outside SQLite's supported range.")

    return sort_order


class CategoryAddModal(discord.ui.Modal):

    def __init__(self):
        super().__init__(title="Create Codex Category")

        self.name_input = discord.ui.TextInput(
            label="Name",
            placeholder="e.g. Deities",
            required=True,
            max_length=50
        )
        self.description_input = discord.ui.TextInput(
            label="Description (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000
        )
        self.icon_input = discord.ui.TextInput(
            label="Icon (optional)",
            placeholder="e.g. ✨",
            required=False,
            max_length=100
        )
        self.sort_order_input = discord.ui.TextInput(
            label="Sort order",
            placeholder="e.g. 60",
            default="0",
            required=True,
            max_length=20
        )

        self.add_item(self.name_input)
        self.add_item(self.description_input)
        self.add_item(self.icon_input)
        self.add_item(self.sort_order_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            sort_order = parse_category_sort_order(
                self.sort_order_input.value
            )
            result = create_category(
                self.name_input.value,
                self.description_input.value.strip() or None,
                self.icon_input.value.strip() or None,
                sort_order
            )
        except ValueError as error:
            await interaction.response.send_message(
                f"❌ {error}",
                ephemeral=True
            )
            return
        except sqlite3.Error:
            await interaction.response.send_message(
                "❌ The category could not be created because of a "
                "database error.",
                ephemeral=True
            )
            return

        name = self.name_input.value.strip()
        if result == "duplicate":
            await interaction.response.send_message(
                f"❌ A category named **{name}** already exists.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ Category **{name}** created.",
            ephemeral=True
        )


class CategoryEditModal(discord.ui.Modal):

    def __init__(self, category):
        category_id, name, description, icon, sort_order = category
        super().__init__(title="Edit Codex Category")
        self.original_name = name

        self.name_input = discord.ui.TextInput(
            label="Name",
            default=name,
            required=True,
            max_length=50
        )
        self.description_input = discord.ui.TextInput(
            label="Description (optional)",
            default=description or "",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000
        )
        self.icon_input = discord.ui.TextInput(
            label="Icon (optional)",
            default=icon or "",
            required=False,
            max_length=100
        )
        self.sort_order_input = discord.ui.TextInput(
            label="Sort order",
            default=str(sort_order),
            required=True,
            max_length=20
        )

        self.add_item(self.name_input)
        self.add_item(self.description_input)
        self.add_item(self.icon_input)
        self.add_item(self.sort_order_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            sort_order = parse_category_sort_order(
                self.sort_order_input.value
            )
            result = update_category(
                self.original_name,
                self.name_input.value,
                self.description_input.value.strip() or None,
                self.icon_input.value.strip() or None,
                sort_order
            )
        except ValueError as error:
            await interaction.response.send_message(
                f"❌ {error}",
                ephemeral=True
            )
            return
        except sqlite3.Error:
            await interaction.response.send_message(
                "❌ The category could not be updated because of a "
                "database error.",
                ephemeral=True
            )
            return

        name = self.name_input.value.strip()
        if result == "not_found":
            message = "❌ That category no longer exists."
        elif result == "duplicate":
            message = f"❌ A category named **{name}** already exists."
        else:
            message = f"✅ Category **{name}** updated."

        await interaction.response.send_message(
            message,
            ephemeral=True
        )


class CategoryDeleteConfirmationView(discord.ui.View):

    def __init__(self, author_id: int, category_name: str):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.category_name = category_name

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "⛔ Only the Worldbuilder who initiated this deletion "
                "can confirm it.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label="Delete Category",
        emoji="🗑️",
        style=discord.ButtonStyle.danger
    )
    async def confirm_delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        try:
            result = delete_category(self.category_name)
        except sqlite3.Error:
            await interaction.response.edit_message(
                content="❌ The category could not be deleted because "
                "of a database error.",
                embed=None,
                view=None
            )
            return

        if result == "not_found":
            message = "❌ That category no longer exists."
        elif result == "in_use":
            message = (
                "❌ This category now contains entries and cannot be "
                "deleted. Reassign those entries first."
            )
        else:
            message = f"🗑️ Category **{self.category_name}** deleted."

        await interaction.response.edit_message(
            content=message,
            embed=None,
            view=None
        )
        self.stop()

    @discord.ui.button(
        label="Cancel",
        emoji="✖️",
        style=discord.ButtonStyle.secondary
    )
    async def cancel_delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        await interaction.response.edit_message(
            content=f"Deletion of **{self.category_name}** cancelled.",
            embed=None,
            view=None
        )
        self.stop()


@bot.tree.command(
    name="wiki-category-add",
    description="Create a registered CoA Codex category."
)
async def wiki_category_add(interaction: discord.Interaction):
    if not await require_worldbuilder(interaction):
        return
    await interaction.response.send_modal(CategoryAddModal())


@bot.tree.command(
    name="wiki-category-edit",
    description="Edit a registered CoA Codex category."
)
@app_commands.describe(category="The category you want to edit.")
@app_commands.autocomplete(category=category_autocomplete)
async def wiki_category_edit(
    interaction: discord.Interaction,
    category: str
):
    if not await require_worldbuilder(interaction):
        return

    details = get_category(category)
    if details is None:
        await interaction.response.send_message(
            f"❌ The category **{category.strip()}** does not exist.",
            ephemeral=True
        )
        return

    await interaction.response.send_modal(CategoryEditModal(details))


@bot.tree.command(
    name="wiki-category-delete",
    description="Delete an empty registered CoA Codex category."
)
@app_commands.describe(category="The empty category you want to delete.")
@app_commands.autocomplete(category=category_autocomplete)
async def wiki_category_delete(
    interaction: discord.Interaction,
    category: str
):
    if not await require_worldbuilder(interaction):
        return

    details = get_category(category)
    if details is None:
        await interaction.response.send_message(
            f"❌ The category **{category.strip()}** does not exist.",
            ephemeral=True
        )
        return

    name = details[1]
    entry_count = get_category_entry_count(name)
    if entry_count:
        noun = "entry" if entry_count == 1 else "entries"
        await interaction.response.send_message(
            f"❌ **{name}** contains {entry_count} {noun} and cannot be "
            "deleted. Reassign those entries first.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="⚠️ Delete Codex Category?",
        description=(
            f"Delete the empty category **{name}**?\n\n"
            "This action cannot be undone."
        ),
        color=discord.Color.red()
    )
    await interaction.response.send_message(
        embed=embed,
        view=CategoryDeleteConfirmationView(
            author_id=interaction.user.id,
            category_name=name
        ),
        ephemeral=True
    )


# --------------------------------------------------
# Category Management Selection
# --------------------------------------------------

class CategoryManagementSelectView(discord.ui.View):

    def __init__(self, author_id: int, mode: str, categories):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.mode = mode

        options = [
            discord.SelectOption(label=category[:100], value=category)
            for category in categories[:25]
        ]

        select = discord.ui.Select(
            placeholder=f"Select a category to {mode}...",
            options=options,
            min_values=1,
            max_values=1
        )
        select.callback = self.select_category
        self.add_item(select)

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "⛔ This category selection belongs to another "
                "Worldbuilder.",
                ephemeral=True
            )
            return False
        return True

    async def select_category(
        self,
        interaction: discord.Interaction
    ):
        category = interaction.data["values"][0]
        details = get_category(category)

        if details is None:
            await interaction.response.send_message(
                "❌ That category no longer exists.",
                ephemeral=True
            )
            return

        if self.mode == "edit":
            await interaction.response.send_modal(
                CategoryEditModal(details)
            )
            return

        name = details[1]
        entry_count = get_category_entry_count(name)
        if entry_count:
            noun = "entry" if entry_count == 1 else "entries"
            await interaction.response.send_message(
                f"❌ **{name}** contains {entry_count} {noun} and "
                "cannot be deleted. Reassign those entries first.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="⚠️ Delete Codex Category?",
            description=(
                f"Delete the empty category **{name}**?\n\n"
                "This action cannot be undone."
            ),
            color=discord.Color.red()
        )
        await interaction.response.send_message(
            embed=embed,
            view=CategoryDeleteConfirmationView(
                author_id=interaction.user.id,
                category_name=name
            ),
            ephemeral=True
        )


# --------------------------------------------------
# Worldbuilder Management View
# --------------------------------------------------

class WikiManagementView(discord.ui.View):

    def __init__(
        self,
        author_id: int
    ):

        super().__init__(
            timeout=300
        )

        self.author_id = author_id

        # --------------------------------------------------
        # Create Entry
        # --------------------------------------------------

        add_button = discord.ui.Button(
            label="Create Entry",
            emoji="➕",
            style=discord.ButtonStyle.success,
            row=0
        )

        add_button.callback = (
            self.create_add_callback()
        )

        self.add_item(add_button)

        # --------------------------------------------------
        # Edit Entry
        # --------------------------------------------------

        edit_button = discord.ui.Button(
            label="Edit Entry",
            emoji="✏️",
            style=discord.ButtonStyle.primary,
            row=0
        )

        edit_button.callback = (
            self.create_edit_callback()
        )

        self.add_item(edit_button)

        # --------------------------------------------------
        # Link Entries
        # --------------------------------------------------

        link_button = discord.ui.Button(
            label="Link Entries",
            emoji="🔗",
            style=discord.ButtonStyle.primary,
            row=0
        )

        link_button.callback = (
            self.create_link_callback()
        )

        self.add_item(link_button)

        # --------------------------------------------------
        # Delete Entry
        # --------------------------------------------------

        delete_button = discord.ui.Button(
            label="Delete Entry",
            emoji="🗑️",
            style=discord.ButtonStyle.danger,
            row=0
        )

        delete_button.callback = (
            self.create_delete_callback()
        )

        self.add_item(delete_button)

        # --------------------------------------------------
        # Category Management
        # --------------------------------------------------

        add_category_button = discord.ui.Button(
            label="Create Category",
            emoji="➕",
            style=discord.ButtonStyle.success,
            row=1
        )
        add_category_button.callback = self.create_category_callback()
        self.add_item(add_category_button)

        edit_category_button = discord.ui.Button(
            label="Edit Category",
            emoji="✏️",
            style=discord.ButtonStyle.primary,
            row=1
        )
        edit_category_button.callback = (
            self.create_category_selection_callback("edit")
        )
        self.add_item(edit_category_button)

        delete_category_button = discord.ui.Button(
            label="Delete Category",
            emoji="🗑️",
            style=discord.ButtonStyle.danger,
            row=1
        )
        delete_category_button.callback = (
            self.create_category_selection_callback("delete")
        )
        self.add_item(delete_category_button)

        # --------------------------------------------------
        # Home
        # --------------------------------------------------

        home_button = discord.ui.Button(
            label="Codex Home",
            emoji="🏠",
            style=discord.ButtonStyle.secondary,
            row=2
        )

        home_button.callback = (
            self.go_home
        )

        self.add_item(home_button)

    # --------------------------------------------------
    # Author Check
    # --------------------------------------------------

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ) -> bool:

        if interaction.user.id != self.author_id:

            await interaction.response.send_message(
                "⛔ This management panel belongs "
                "to the Worldbuilder who opened it.",
                ephemeral=True
            )

            return False

        return True

    # --------------------------------------------------
    # Create Entry
    # --------------------------------------------------

    def create_add_callback(self):

        async def callback(
            interaction: discord.Interaction
        ):

            await send_entry_category_picker(
                interaction,
                mode="add"
            )

        return callback

    # --------------------------------------------------
    # Edit Entry
    # --------------------------------------------------

    def create_edit_callback(self):

        async def callback(
            interaction: discord.Interaction
        ):

            await interaction.response.send_modal(
                WikiEntrySearchModal(
                    mode="edit",
                    author_id=interaction.user.id
                )
            )

        return callback

    # --------------------------------------------------
    # Link Entries
    # --------------------------------------------------

    def create_link_callback(self):

        async def callback(
            interaction: discord.Interaction
        ):

            await interaction.response.send_modal(
                WikiEntrySearchModal(
                    mode="link-source",
                    author_id=interaction.user.id
                )
            )

        return callback

    # --------------------------------------------------
    # Delete Entry
    # --------------------------------------------------

    def create_delete_callback(self):

        async def callback(
            interaction: discord.Interaction
        ):

            await interaction.response.send_modal(
                WikiEntrySearchModal(
                    mode="delete",
                    author_id=interaction.user.id
                )
            )

        return callback

    # --------------------------------------------------
    # Category Management
    # --------------------------------------------------

    def create_category_callback(self):

        async def callback(
            interaction: discord.Interaction
        ):
            await interaction.response.send_modal(
                CategoryAddModal()
            )

        return callback

    def create_category_selection_callback(self, mode: str):

        async def callback(
            interaction: discord.Interaction
        ):
            try:
                categories = get_categories()
            except sqlite3.Error:
                await interaction.response.send_message(
                    "❌ Categories could not be loaded because of a "
                    "database error.",
                    ephemeral=True
                )
                return

            if not categories:
                await interaction.response.send_message(
                    "❌ No registered categories are available.",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"Select a category to {mode}.",
                view=CategoryManagementSelectView(
                    author_id=interaction.user.id,
                    mode=mode,
                    categories=categories
                ),
                ephemeral=True
            )

        return callback

    # --------------------------------------------------
    # Home
    # --------------------------------------------------

    async def go_home(
        self,
        interaction: discord.Interaction
    ):

        await display_codex_home(
            interaction,
            edit=True,
            history=[]
        )

# --------------------------------------------------
# /wiki-manage
# --------------------------------------------------

@bot.tree.command(
    name="wiki-manage",
    description="Open the Worldbuilder CoA Codex management panel."
)
async def wiki_manage(
    interaction: discord.Interaction
):

    if not await require_worldbuilder(interaction):
        return

    embed = discord.Embed(
        title="🛠️ Codex Management",
        description=(
            "Manage the CoA Codex from this panel.\n\n"
            "Create, edit, connect, or delete codex entries and "
            "categories."
        ),
        color=discord.Color.gold()
    )

    embed.add_field(
        name="➕ Create Entry",
        value=(
            "Create a new codex entry "
            "using the entry editor."
        ),
        inline=True
    )

    embed.add_field(
        name="✏️ Edit Entry",
        value=(
            "Modify an existing codex entry."
        ),
        inline=True
    )

    embed.add_field(
        name="🔗 Link Entries",
        value=(
            "Create a relationship between "
            "two wiki entries."
        ),
        inline=True
    )

    embed.add_field(
        name="🗑️ Delete Entry",
        value=(
            "Permanently remove an entry "
            "from the Codex."
        ),
        inline=True
    )

    embed.add_field(
        name="📚 Manage Categories",
        value=(
            "Create, edit, or delete registered Codex categories."
        ),
        inline=False
    )

    embed.set_footer(
        text=(
            "Worldbuilder tools • "
            "Only you can use this panel."
        )
    )

    view = WikiManagementView(
        author_id=interaction.user.id
    )

    await interaction.response.send_message(
        embed=embed,
        view=view,
        ephemeral=True
    )

# --------------------------------------------------
# Start Bot
# --------------------------------------------------

if not TOKEN:

    raise RuntimeError(
        "DISCORD_TOKEN was not found. "
        "Check your .env file."
    )

if not DISCORD_GUILD_IDS:

    raise RuntimeError(
        "DISCORD_GUILD_IDS was not found. "
        "Check your .env file."
    )


if not WORLD_BUILDER_ROLE_IDS:

    raise RuntimeError(
        "WORLD_BUILDER_ROLE_IDS was not found. "
        "Check your .env file."
    )

bot.run(TOKEN)
