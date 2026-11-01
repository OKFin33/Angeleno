"""Private continuity store; model proposals are evidence-bound transactional writes."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from .author import MAX_HISTORY_MESSAGES


APPLICATION_ID = 0x48455231
KINDS = {"character", "user", "relationship", "active", "episode"}
CONTEXT_LIMIT = 16_000
READBACK_LIMIT = 2_000
RECORD_FIELDS = {"id", "kind", "text", "keywords", "evidence", "known"}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def seed_digest(pack):
    return hashlib.sha256(encoded(pack.provenance()).encode()).hexdigest()


def inputs(records, events):
    """Content-free provenance of what a model could have used, not claimed citations."""
    return {"records": [{"id": r["id"], "revision": r["revision"]} for r in records],
            "events": sorted(set(events))}


def depends_on(sources, ids, evidence):
    return (any(r["id"] in ids for r in sources.get("records", []))
            or bool(evidence.intersection(sources.get("events", []))))


def terms(text):
    """Small transparent Chinese/Latin lexical index, not semantic search."""
    out = set(re.findall(r"[a-z0-9_]{2,}", text.lower()))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out - {"这个", "那个", "我们", "你们", "他们", "什么", "一下", "觉得", "可以", "没有"}


class StaleBatch(ValueError):
    pass


class BacklogFull(ValueError):
    pass


@dataclass(frozen=True)
class Batch:
    revision: int
    watermark: int
    through: int
    events: tuple
    records: tuple


class MemoryStore:
    def __init__(self, path, *, character, user, seed):
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError("Memory database must not be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.lock = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.lock)
            raise ValueError("This character/user memory is already open in another process") from None
        existing = self.path.exists()
        try:
            self.db = sqlite3.connect(self.path, timeout=2)
        except Exception:
            os.close(self.lock)
            raise
        self.db.row_factory = sqlite3.Row
        try:
            if existing and self.db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise ValueError("Not a Her memory database; left untouched")
            self.path.chmod(0o600)
            self.db.execute("PRAGMA secure_delete=ON")
            if not existing:
                self.db.executescript(f"""
                    PRAGMA application_id={APPLICATION_ID};
                    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE events (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL,
                        role TEXT NOT NULL, text TEXT NOT NULL, delivery TEXT NOT NULL,
                        created TEXT NOT NULL, turn INTEGER NOT NULL DEFAULT 0);
                    CREATE TABLE records (id TEXT PRIMARY KEY, payload TEXT NOT NULL, active INTEGER NOT NULL,
                                          kind TEXT NOT NULL, known INTEGER NOT NULL, updated INTEGER NOT NULL);
                    CREATE TABLE terms (record_id TEXT NOT NULL, term TEXT NOT NULL, weight INTEGER NOT NULL,
                                        PRIMARY KEY(record_id,term));
                    CREATE INDEX terms_lookup ON terms(term);
                    CREATE TABLE revisions (id INTEGER PRIMARY KEY, record_id TEXT NOT NULL, payload TEXT NOT NULL);
                    CREATE TABLE turn_inputs (turn INTEGER PRIMARY KEY, payload TEXT NOT NULL);
                """)
                with self.db:
                    for key, value in {"schema": "2", "character": character, "user": user, "seed": seed,
                                       "revision": "0", "watermark": "0"}.items():
                        self.db.execute("INSERT INTO meta VALUES (?,?)", (key, value))
            for key, value in {"schema": "2", "character": character, "user": user, "seed": seed}.items():
                if self._meta(key) != value:
                    raise ValueError(f"Memory {key} differs; explicit migration is required")
        except Exception:
            self.db.close()
            os.close(self.lock)
            raise

    def close(self):
        self.db.close()
        os.close(self.lock)

    def backup(self, target):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        destination = sqlite3.connect(target)
        try:
            self.db.backup(destination)
        finally:
            destination.close()

    def _meta(self, key):
        return self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0]

    @property
    def revision(self):
        return int(self._meta("revision"))

    @property
    def watermark(self):
        return int(self._meta("watermark"))

    def _set_meta(self, key, value):
        self.db.execute("UPDATE meta SET value=? WHERE key=?", (str(value), key))

    def append(self, session, role, text, *, delivery="complete"):
        if role not in {"user", "assistant"} or delivery not in {"complete", "interrupted"}:
            raise ValueError("Invalid exchange evidence")
        if not isinstance(text, str) or not text.strip() or len(text) > 6_000:
            raise ValueError("Each exchange must contain 1–6000 characters")
        with self.db:
            parent = None
            if role == "assistant":
                parent = self.db.execute("SELECT seq FROM events WHERE session=? AND role='user' AND delivery!='forgotten' ORDER BY seq DESC LIMIT 1", (session,)).fetchone()
                if parent is None:
                    raise ValueError("Delivered assistant speech needs a user turn")
            cursor = self.db.execute("INSERT INTO events(session,role,text,delivery,created) VALUES (?,?,?,?,?)",
                                     (session, role, text, delivery, datetime.now(timezone.utc).isoformat()))
            self.db.execute("UPDATE events SET turn=? WHERE seq=?", (parent[0] if parent else cursor.lastrowid, cursor.lastrowid))
        return cursor.lastrowid

    def records(self, *, include_hidden=False):
        records = [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM records WHERE active=1 ORDER BY rowid")]
        return [r for r in records if include_hidden or r["known"]]

    def pending(self):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM events WHERE seq>? AND delivery!='forgotten' ORDER BY seq", (self.watermark,))]

    def select(self, query):
        cards = [json.loads(r[0]) for r in self.db.execute(
            "SELECT payload FROM records WHERE active=1 AND known=1 AND kind!='episode' ORDER BY id")]
        query_terms = sorted(terms(query))[:500]
        episodes = []
        if query_terms:
            placeholders = ",".join("?" for _ in query_terms)
            episodes = [json.loads(r[0]) for r in self.db.execute(
                f"SELECT r.payload FROM records r JOIN terms t ON r.id=t.record_id "
                f"WHERE r.active=1 AND r.known=1 AND r.kind='episode' AND t.term IN ({placeholders}) "
                "GROUP BY r.id ORDER BY SUM(t.weight) DESC,r.updated DESC,r.id DESC LIMIT 6", query_terms)]
        # Explicit references to a previous conversation can use a small recency fallback.
        if not episodes and any(word in query for word in ("上次", "之前", "那个", "后来")):
            episodes = [json.loads(r[0]) for r in self.db.execute(
                "SELECT payload FROM records WHERE active=1 AND known=1 AND kind='episode' ORDER BY updated DESC,rowid DESC LIMIT 3")]
        selected = cards + episodes
        while len(encoded(selected)) > 10_000 and len(selected) > len(cards):
            selected.pop()
        if len(encoded(selected)) > 10_000:
            raise BacklogFull("Current cards exceed the context budget; inspect and revise memory")
        return selected

    def context(self, session, query):
        # A call boundary does not erase recent conversation. Maintenance moves
        # the watermark, not this bounded raw window, so an immediate redial can
        # resolve references without a keyword match or a search tool. Prior
        # unprocessed turns retain their existing full-backlog budget below.
        recent = [dict(r) for r in self.db.execute(
            "SELECT * FROM events WHERE session=? OR seq<=? ORDER BY seq DESC LIMIT ?",
            (session, self.watermark, MAX_HISTORY_MESSAGES))][::-1]
        hidden = {seq for row in self.db.execute("SELECT payload FROM records WHERE known=0")
                  for seq in json.loads(row[0])["evidence"]}
        turns = {r["turn"] for r in recent}
        partners = self.db.execute(
            f"SELECT seq,turn,delivery FROM events WHERE turn IN ({','.join('?' for _ in turns)})",
            tuple(turns)) if turns else ()
        unavailable_turns = {r["turn"] for r in partners
                             if r["delivery"] == "forgotten" or r["seq"] in hidden}
        recent = [r for r in recent if r["turn"] not in unavailable_turns]
        # Match the compiler's usable window before removing these events from
        # unprocessed. Otherwise its leading-assistant trim loses pending evidence.
        while recent and recent[0]["role"] == "assistant":
            recent.pop(0)
        ids = {r["seq"] for r in recent}
        backlog = [r for r in self.pending() if r["seq"] not in ids]
        context = {"revision": self.revision, "records": self.select(query), "unprocessed": backlog,
                   "history_evidence": sorted(ids),
                   "history_sources": [{k: r[k] for k in ("seq", "session", "role", "created", "delivery")} for r in recent]}
        # Keep all-citation time anchors compact. Optional raw turns below use
        # only spare space; maintenance still reads full direct evidence.
        context["record_sources"] = self.record_sources(context["records"])
        # Reuse the delivered-result projection for the same complete raw
        # utterances the actor actually sees, not every lookup in this identity.
        sources = self.maintenance_lookup_sources((*recent, *backlog))
        if sources["results"]:
            context["lookup_sources"] = sources
        if len(encoded(context)) > CONTEXT_LIMIT:
            raise BacklogFull("Continuity sources exceed the budget; inspect memory and pending maintenance before continuing")
        # Optional source material uses only remaining space; it never displaces
        # records, unprocessed corrections or the existing provenance summaries.
        budget = min(READBACK_LIMIT - len('{"source_readback":}'),
                     CONTEXT_LIMIT - len(encoded(context)) - len(',"source_readback":'))
        readback = self.source_readback(context["records"], query,
                                        ids | {e["seq"] for e in backlog}, budget)
        if readback is not None:
            candidate = {**context, "source_readback": readback}
            sources = self.maintenance_lookup_sources((*recent, *backlog, *readback["events"]))
            if sources["results"]:
                candidate["lookup_sources"] = sources
            # Optional raw readback and its source records travel together;
            # neither may displace mandatory corrections or hide their grade.
            if len(encoded(candidate)) <= CONTEXT_LIMIT:
                context = candidate
        history = [{"role": r["role"], "content": r["text"] if r["delivery"] == "complete"
                    else "[交付中断或范围不确定的发言] " + r["text"]} for r in recent]
        return context, history

    def capture_input(self, turn, context):
        """Save exposure before sending a compiled actor request; no private turns call this."""
        sources = inputs(context["records"], [*context["history_evidence"],
                                              *(e["seq"] for e in context["unprocessed"]),
                                              *(e["seq"] for e in context.get("source_readback", {}).get("events", []))])
        with self.db:
            self.db.execute("INSERT INTO turn_inputs VALUES (?,?)", (turn, encoded(sources)))

    def current_turn(self, session):
        """Latest saved user turn, for opt-in actor tool exposure only."""
        row = self.db.execute("SELECT seq FROM events WHERE session=? AND role='user' "
                              "AND delivery!='forgotten' ORDER BY seq DESC LIMIT 1", (session,)).fetchone()
        if row is None:
            raise ValueError("No saved user turn")
        return row[0]

    def extend_input(self, turn, records, events):
        """Union actual tool exposure with the input already captured by begin()."""
        row = self.db.execute("SELECT payload FROM turn_inputs WHERE turn=?", (turn,)).fetchone()
        if row is None:
            raise ValueError("Tool exposure requires an existing saved actor input")
        prior = json.loads(row[0])
        versions = {(r["id"], r["revision"]) for r in prior["records"]}
        versions.update((r["id"], r["revision"]) for r in records)
        sources = {"records": [{"id": rid, "revision": revision} for rid, revision in sorted(versions)],
                   "events": sorted(set(prior["events"]) | set(events))}
        with self.db:
            self.db.execute("UPDATE turn_inputs SET payload=? WHERE turn=?", (encoded(sources), turn))

    def search(self, query, *, budget):
        """Bounded lexical memory.search; no unrelated cards or recency fallback.

        Return complete selected items and optional original turns using the same
        visibility rules as preloading. The caller captures this exact result
        synchronously before any maintenance coroutine can change its versions.
        """
        query_terms = sorted(terms(query))[:500]
        ranked = []
        if query_terms:
            ranked = [json.loads(r[0]) for r in self.db.execute(
                "SELECT r.payload FROM records r JOIN terms t ON r.id=t.record_id "
                "WHERE r.active=1 AND r.known=1 AND t.term IN (" + ",".join("?" for _ in query_terms) + ") "
                "GROUP BY r.id ORDER BY SUM(t.weight) DESC,r.updated DESC,r.id DESC LIMIT 6", query_terms)]
        value = {"status": "no_match", "records": [], "record_sources": []}
        if len(encoded(value)) > budget:
            raise ValueError("No room for an honest memory search result")
        omitted = False
        for record in ranked:
            candidate = {"status": "matched", "records": [*value["records"], record],
                         "record_sources": self.record_sources([*value["records"], record])}
            if len(encoded(candidate)) > budget:
                omitted = True
                continue  # Entire item; no clipping summary or original speech.
            value = candidate
        if omitted:
            candidate = {**value, "status": "partial" if value["records"] else "budget_omitted"}
            if len(encoded(candidate)) <= budget:
                value = candidate
            else:
                raise ValueError("No room for honest memory omission status")
        spare = min(READBACK_LIMIT, budget - len(encoded(value)) - len(',"source_readback":'))
        readback = self.source_readback(value["records"], query, set(), spare)
        if readback is not None:
            value["source_readback"] = readback
        return value

    def batch(self):
        pending = self.pending()
        if not pending:
            return None
        selected, size = [], 0
        for event in pending[:12]:
            if selected and size + len(event["text"]) > 12_000:
                break
            selected.append(event)
            size += len(event["text"])
        query = "\n".join(e["text"] for e in selected)
        return Batch(self.revision, self.watermark, selected[-1]["seq"], tuple(selected), tuple(self.select(query)))

    def record_evidence(self, records, *, include_text=True):
        """Direct source data, separate from deletion-only consumption inputs.

        Maintenance reads complete sources. Actor context retains bounded range
        summaries, with only a small optional related-turn readback.
        """
        ids = sorted({n for r in records for n in r["evidence"]})
        if not ids:
            return []
        columns = "*" if include_text else "seq,session,role,created,delivery"
        rows = self.db.execute(f"SELECT {columns} FROM events WHERE seq IN ({','.join('?' for _ in ids)}) "
                               "AND delivery='complete' ORDER BY seq", ids)
        return [dict(r) for r in rows]

    def record_sources(self, records):
        """Bounded provenance per selected record, never per-clause event timing.

        A range covers all unique direct citations of this record. A single
        source has equal endpoints; multiple sources cannot locate any clause.
        Keep original timestamps rather than interpreting the text's dates.
        """
        events = {e["seq"]: e for e in self.record_evidence(records, include_text=False)}
        summaries = []
        for record in records:
            sources = [events[n] for n in sorted(set(record["evidence"])) if n in events]
            if not sources:
                continue
            ordered = sorted(sources, key=lambda e: datetime.fromisoformat(e["created"]))
            summaries.append({"id": record["id"], "scope": "all_record_evidence",
                              "count": len(sources), "roles": sorted({e["role"] for e in sources}),
                              "created_range": [ordered[0]["created"], ordered[-1]["created"]]})
        return summaries

    def source_readback(self, records, query, excluded, budget):
        """At most two selected known records, latest cited turn only, no clipping.

        Rank with the existing lexical index weights (keyword=5, text=1),
        then revision/id. Zero-score cards are unrelated; selected episodes may
        also come from select()'s existing explicit-reference recency fallback.
        Citation/turn exposure is not a claim that a summary is supported.
        """
        query_terms = set(sorted(terms(query))[:500])
        ranked = []
        for record in records:
            if not record["known"]:
                continue
            keys, words = terms(" ".join(record["keywords"])), terms(record["text"])
            score = sum(5 if word in keys else 1 for word in query_terms & (keys | words))
            if score or record["kind"] == "episode":
                ranked.append((score, record))
        selected = [r for _, r in sorted(ranked, key=lambda item: (-item[0], -item[1]["revision"], item[1]["id"]))[:2]]
        if not selected:
            return None
        value = {"scope": "partial_latest_cited_turn", "records": [
            {"id": r["id"], "read_seq": [], "omitted_evidence": len(set(r["evidence"])), "omitted": "budget"}
            for r in selected], "events": []}
        if len(encoded(value)) > budget:
            return None  # Even omission status must not break a usable old input.
        # A public record's turn partner must not bypass a hidden record's
        # sources. Retirement does not make a hidden source public either.
        hidden = {seq for row in self.db.execute("SELECT payload FROM records WHERE known=0")
                  for seq in json.loads(row[0])["evidence"]}
        for record, entry in zip(selected, value["records"]):
            latest = max(record["evidence"])
            source = self.db.execute("SELECT * FROM events WHERE seq=? AND delivery='complete'", (latest,)).fetchone()
            if source is None or not source["turn"]:
                entry["omitted"] = "unavailable"
                continue  # Never fall back to an older citation.
            turn = [dict(e) for e in self.db.execute(
                "SELECT * FROM events WHERE session=? AND turn=? ORDER BY seq", (source["session"], source["turn"]))]
            if any(e["delivery"] == "forgotten" or e["seq"] in hidden for e in turn):
                entry["omitted"] = "unavailable"
                continue
            visible = [e for e in turn if e["seq"] not in excluded]
            if not visible:
                entry["omitted"] = "already_in_input"
                continue
            old_entry, old_events = entry.copy(), value["events"]
            present = {e["seq"] for e in old_events}
            value["events"] = sorted([*old_events, *(e for e in visible if e["seq"] not in present)], key=lambda e: e["seq"])
            entry["read_seq"] = [e["seq"] for e in visible]
            entry["omitted_evidence"] = len(set(record["evidence"]) - set(entry["read_seq"]))
            entry.pop("omitted")
            if len(value["events"]) > 4 or len(encoded(value)) > budget:
                value["events"] = old_events
                entry.clear()
                entry.update(old_entry)  # Entire new turn omitted, no shorter/older substitute.
        # Longer status words can also exceed an extremely narrow remainder.
        return value if len(encoded(value)) <= budget else None

    def apply(self, batch, proposal):
        """Commit one complete proposal, or nothing; no semantic-truth guarantee."""
        if not isinstance(proposal, dict) or set(proposal) != {"updates", "retire"}:
            raise ValueError("Maintenance output must have updates and retire")
        updates, retire = proposal["updates"], proposal["retire"]
        if not isinstance(updates, list) or not isinstance(retire, list) or len(updates) + len(retire) > 20:
            raise ValueError("Maintenance proposal is too large")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.revision != batch.revision or self.watermark != batch.watermark:
                raise StaleBatch("Memory changed while this batch was being prepared; proposal discarded")
            visible_records = {r["id"]: r for r in batch.records}
            visible = set(visible_records)
            allowed = {e["seq"] for e in batch.events if e["delivery"] == "complete"}
            allowed.update(n for r in batch.records for n in r["evidence"])
            current_events = {r["seq"]: dict(r) for r in self.db.execute(
                "SELECT * FROM events WHERE seq<=? AND delivery='complete'", (batch.through,))}
            ids = set()
            validated = []
            for value in updates:
                if not isinstance(value, dict) or set(value) != RECORD_FIELDS:
                    raise ValueError("Invalid memory record fields")
                r = dict(value)
                rid, kind = r["id"], r["kind"]
                if not isinstance(rid, str) or not re.fullmatch(r"[a-z0-9_.:-]{1,80}", rid) or rid in ids:
                    raise ValueError("Invalid or repeated record id")
                ids.add(rid)
                if kind not in KINDS or (kind != "episode" and rid != "card." + kind):
                    raise ValueError("Current cards must use their stable id")
                if kind == "episode" and not rid.startswith("episode."):
                    raise ValueError("Episode id must start with episode.")
                if not isinstance(r["text"], str) or not r["text"].strip() or len(r["text"]) > (1600 if kind != "episode" else 1000):
                    raise ValueError("Memory content exceeds its budget")
                if type(r["known"]) is not bool:
                    raise ValueError("known must be explicit")
                if not isinstance(r["keywords"], list) or len(r["keywords"]) > 12 or any(
                        not isinstance(k, str) or not 1 <= len(k) <= 40 for k in r["keywords"]):
                    raise ValueError("Invalid memory keywords")
                ev = r["evidence"]
                if not isinstance(ev, list) or not 1 <= len(ev) <= 64 or any(type(n) is not int for n in ev):
                    raise ValueError("Memory requires evidence ids")
                if not set(ev) <= allowed or not set(ev) <= current_events.keys():
                    raise ValueError("Memory cites unseen, deleted or uncompleted evidence")
                old = visible_records.get(rid)
                # Validate even redundant proposals first; malformed citations and
                # hidden/revived records must never be repaired by no-op handling.
                existing = self.db.execute("SELECT active,known FROM records WHERE id=?", (rid,)).fetchone()
                if existing:
                    if existing["active"] and rid not in visible:
                        raise ValueError("Cannot overwrite hidden memory")
                    if not existing["active"] and (kind == "episode" or not existing["known"]):
                        raise ValueError("Cannot revive retired episodes or hidden memory")
                if old and old["kind"] != kind:
                    raise ValueError("Cannot change an existing record kind")
                if old and all(r[k] == old[k] for k in RECORD_FIELDS):
                    # All six fields, including citation order, match exactly.
                    # Preserve bytes, revision and deletion lineage.
                    continue
                if not any(batch.watermark < n <= batch.through for n in ev):
                    raise ValueError("Memory update must cite the current batch")
                roles = {current_events[n]["role"] for n in ev}
                if kind == "user" and "user" not in roles:
                    raise ValueError("User information cannot be sourced only from assistant speech")
                if kind == "character" and "assistant" not in roles:
                    raise ValueError("Character additions require delivered character speech")
                if kind in {"user", "character"}:
                    required_role = "user" if kind == "user" else "assistant"
                    if not any(n > batch.watermark and current_events[n]["role"] == required_role for n in ev):
                        raise ValueError("Changed card requires new evidence from its source speaker")
                r["revision"] = self.revision + 1
                validated.append(r)
            if any(not isinstance(rid, str) for rid in retire) or len(set(retire)) != len(retire) or any(rid not in visible or rid in ids for rid in retire):
                raise ValueError("Only exposed existing records can be retired, once")
            for r in validated:
                # A batch may replace several related memories. Preserve conservative
                # lineage so forgetting the replacement also removes retired versions.
                r["replaces"] = list(retire)
                # Model citations can omit an input it copied or paraphrased. Keep
                # actual batch exposure as conservative deletion lineage as well.
                r["inputs"] = inputs(batch.records, (e["seq"] for e in batch.events))
            changed_ids = {r["id"] for r in validated}
            next_cards = [r for r in batch.records if r["kind"] != "episode" and r["id"] not in changed_ids and r["id"] not in retire]
            next_cards += [r for r in validated if r["kind"] != "episode" and r["known"]]
            if len(encoded(next_cards)) > 10_000:
                raise ValueError("Proposed current cards exceed the usable context budget")
            for rid in [*retire, *(r["id"] for r in validated)]:
                old = self.db.execute("SELECT payload FROM records WHERE id=?", (rid,)).fetchone()
                if old:
                    self.db.execute("INSERT INTO revisions(record_id,payload) VALUES (?,?)", (rid, old[0]))
            for rid in retire:
                self.db.execute("UPDATE records SET active=0 WHERE id=?", (rid,))
                self.db.execute("DELETE FROM terms WHERE record_id=?", (rid,))
            for r in validated:
                self.db.execute("INSERT INTO records VALUES (?,?,1,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,active=1,kind=excluded.kind,known=excluded.known,updated=excluded.updated",
                                (r["id"], encoded(r), r["kind"], int(r["known"]), r["revision"]))
                self.db.execute("DELETE FROM terms WHERE record_id=?", (r["id"],))
                words, keys = terms(r["text"]), terms(" ".join(r["keywords"]))
                self.db.executemany("INSERT INTO terms VALUES (?,?,?)",
                                    [(r["id"], term, 5 if term in keys else 1) for term in words | keys])
            self._set_meta("revision", self.revision + 1)
            self._set_meta("watermark", batch.through)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def _ensure_lookup_table(self):
        # Additive result log. The schema meta value stays 2; opening an older
        # database does not create this table and does not rewrite existing rows.
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS lookup_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session TEXT NOT NULL,
                user_turn INTEGER NOT NULL,
                request_id TEXT NOT NULL UNIQUE,
                assistant_seq INTEGER NOT NULL,
                status TEXT NOT NULL,
                subject TEXT NOT NULL,
                conclusion TEXT,
                applicable_date TEXT,
                checked_at TEXT,
                source_id TEXT,
                source_url TEXT,
                excerpt TEXT,
                delivered_text TEXT NOT NULL
            )
        """)
        # Additive, only on a delivered-result write. Reading/opening an older
        # store never migrates it or guesses missing evidence grades.
        if "source_context" not in {row[1] for row in self.db.execute("PRAGMA table_info(lookup_results)")}:
            self.db.execute("ALTER TABLE lookup_results ADD COLUMN source_context TEXT")

    def _lookup_table_exists(self):
        return self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lookup_results'",
        ).fetchone() is not None

    def remember_lookup(self, session, payload):
        """Store one delivered source result. Refuses text that is not the stored reply."""
        required = ("user_turn", "request_id", "status", "subject", "delivered_text")
        if any(key not in payload for key in required):
            return None
        if type(payload["user_turn"]) is not int or payload["user_turn"] < 1:
            return None
        if not isinstance(payload["delivered_text"], str) or not payload["delivered_text"].strip():
            return None
        if not isinstance(payload["request_id"], str) or not payload["request_id"]:
            return None
        row = self.db.execute(
            "SELECT seq, text, turn FROM events WHERE session=? AND role='assistant' "
            "AND delivery='complete' ORDER BY seq DESC LIMIT 1",
            (session,),
        ).fetchone()
        if row is None or row["text"] != payload["delivered_text"] or row["turn"] != payload["user_turn"]:
            return None
        context = payload.get("source_context")
        if context is not None and not isinstance(context, dict):
            raise ValueError("Lookup source context must be an object")
        # SAVEPOINT includes additive DDL and the source row in one write;
        # a failed insert must not leave a half-applied legacy upgrade.
        self.db.execute("SAVEPOINT delivered_lookup_source")
        try:
            self._ensure_lookup_table()
            self.db.execute(
                "INSERT INTO lookup_results(session,user_turn,request_id,assistant_seq,status,subject,"
                "conclusion,applicable_date,checked_at,source_id,source_url,excerpt,delivered_text,source_context) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    session, payload["user_turn"], payload["request_id"], row["seq"], payload["status"],
                    payload["subject"], payload["conclusion"], payload["applicable_date"], payload["checked_at"],
                    payload.get("source_id"), payload.get("source_url"), payload.get("excerpt"),
                    payload["delivered_text"], None if context is None else encoded(context),
                ),
            )
            self.db.execute("RELEASE delivered_lookup_source")
        except BaseException as exc:
            self.db.execute("ROLLBACK TO delivered_lookup_source")
            self.db.execute("RELEASE delivered_lookup_source")
            if isinstance(exc, sqlite3.IntegrityError):
                return None
            raise
        return row["seq"]

    def lookup_results(self, *, assistant_seqs=None):
        if not self._lookup_table_exists():
            return []
        ids = None if assistant_seqs is None else sorted(set(assistant_seqs))
        if ids == []:
            return []
        context = ("source_context" if "source_context" in {
            row[1] for row in self.db.execute("PRAGMA table_info(lookup_results)")}
            else "NULL AS source_context")
        where = "" if ids is None else " WHERE assistant_seq IN (" + ",".join("?" for _ in ids) + ")"
        rows = [dict(row) for row in self.db.execute(
            "SELECT session,user_turn,request_id,assistant_seq,status,subject,conclusion,applicable_date,"
            "checked_at,source_id,source_url,excerpt,delivered_text," + context + " FROM lookup_results" + where + " ORDER BY id",
            () if ids is None else ids,
        )]
        for row in rows:
            row["source_context"] = None if row["source_context"] is None else json.loads(row["source_context"])
        return rows

    def maintenance_lookup_sources(self, events):
        """Only records tied to the exact raw evidence exposed to this writer.

        This is a delivered-result log, not an exhaustive tool-attempt journal.
        Missing/legacy data cannot certify either a fetch or that none occurred.
        """
        visible = {event["seq"] for event in events if event["delivery"] == "complete"}
        rows = []
        for result in self.lookup_results(assistant_seqs=visible):
            row = {key: value for key, value in result.items() if key != "delivered_text"}
            context = row["source_context"] or {}
            reading = context.get("reading") or {}
            evidence_reading = reading.get("evidence_reading") or {}
            scope = evidence_reading.get("observation_scope")
            known_scopes = {"synthetic_fixture", "live_http_page", "recorded_query_empty", "recorded_query_results",
                            "recorded_page", "replay_query_unobserved", "replay_body_unobserved",
                            "live_query_empty", "live_query_results", "live_page"}
            row["source_context_status"] = scope if isinstance(scope, str) and scope in known_scopes else "unknown"
            rows.append(row)
        return {"coverage": "delivered_results_for_exposed_evidence_only", "results": rows,
                "missing_record": "no_recorded_delivered_result_not_proof_of_no_attempt"}

    def _drop_lookup_results(self, seqs=None):
        if not self._lookup_table_exists():
            return
        if seqs is None:
            self.db.execute("DELETE FROM lookup_results")
            return
        seqs = tuple(seqs)
        if not seqs:
            return
        placeholders = ",".join("?" for _ in seqs)
        self.db.execute(
            f"DELETE FROM lookup_results WHERE assistant_seq IN ({placeholders})", seqs,
        )

    def forget(self, record_id=None):
        """Conservatively remove evidence and all summaries derived from it, including revisions."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            all_records = [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM records UNION ALL SELECT payload FROM revisions")]
            if record_id is None:
                self.db.execute("DELETE FROM records")
                self.db.execute("DELETE FROM revisions")
                self.db.execute("DELETE FROM terms")
                self.db.execute("DELETE FROM turn_inputs")
                self.db.execute("UPDATE events SET text='',delivery='forgotten'")
                self._drop_lookup_results(None)
                count = len({r["id"] for r in all_records})
            else:
                found = [r for r in all_records if r["id"] == record_id]
                if not found:
                    raise ValueError("Memory id not found")
                ids, evidence = {record_id}, {n for r in found for n in r["evidence"]}
                turns = {}
                for event in self.db.execute("SELECT seq,turn FROM events"):
                    turns.setdefault(event["turn"], set()).add(event["seq"])
                reads = [(r[0], json.loads(r[1])) for r in self.db.execute("SELECT turn,payload FROM turn_inputs")]
                while True:
                    next_ev = set(evidence)
                    # A reply may repeat a forgotten user fact even when no card cited
                    # that reply. Remove the whole delivered exchange, not just its citation.
                    for seqs in turns.values():
                        if evidence.intersection(seqs):
                            next_ev.update(seqs)
                    # Follow later actor turns across sessions, even when their output
                    # was never cited by maintenance or repeated the fact indirectly.
                    for turn, sources in reads:
                        if depends_on(sources, ids, next_ev):
                            next_ev.update(turns.get(turn, ()))
                    related = [r for r in all_records if r["id"] in ids or next_ev.intersection(r["evidence"])
                               or ids.intersection(r.get("replaces", []))
                               or depends_on(r.get("inputs", {}), ids, next_ev)]
                    next_ids = ids | {r["id"] for r in related} | {rid for r in related for rid in r.get("replaces", [])}
                    next_ev.update(n for r in related for n in r["evidence"])
                    if next_ids == ids and next_ev == evidence:
                        break
                    ids, evidence = next_ids, next_ev
                for rid in ids:
                    self.db.execute("DELETE FROM records WHERE id=?", (rid,))
                    self.db.execute("DELETE FROM revisions WHERE record_id=?", (rid,))
                    self.db.execute("DELETE FROM terms WHERE record_id=?", (rid,))
                for seq in evidence:
                    self.db.execute("UPDATE events SET text='',delivery='forgotten' WHERE seq=?", (seq,))
                    self.db.execute("DELETE FROM turn_inputs WHERE turn=?", (seq,))
                self._drop_lookup_results(evidence)
                count = len(ids)
            self._set_meta("revision", self.revision + 1)
            self.db.commit()
            return count
        except Exception:
            self.db.rollback()
            raise


def new_session():
    return uuid.uuid4().hex
