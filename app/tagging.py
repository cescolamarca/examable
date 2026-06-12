from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

from app.config import settings

BASE_TAGS: list[tuple[str, str]] = [
    ("reti", "Reti"),
    ("tcp", "TCP"),
    ("udp", "UDP"),
    ("dns", "DNS"),
    ("http", "HTTP"),
    ("smtp", "SMTP"),
    ("dhcp", "DHCP"),
    ("icmp", "ICMP"),
    ("ipv4", "IPv4"),
    ("ipv6", "IPv6"),
    ("ethernet", "Ethernet"),
    ("routing", "Routing"),
    ("nat", "NAT"),
    ("subnetting", "Subnetting"),
    ("arq", "ARQ"),
    ("sicurezza", "Sicurezza"),
    ("tls", "TLS"),
    ("firewall", "Firewall"),
    # Modulo 1 preset tags
    ("introduzione-reti", "Introduzione alle reti"),
    ("internet-ict-protocolli", "Internet, ICT e protocolli"),
    ("architettura-livelli", "Architettura a livelli"),
    ("pila-protocolli-internet", "Pila protocolli Internet"),
    ("incapsulamento", "Incapsulamento"),
    ("network-edge", "Network Edge"),
    ("host-sistemi-periferici", "Host (sistemi periferici)"),
    ("reti-accesso", "Reti di accesso"),
    ("mezzi-trasmissivi", "Mezzi trasmissivi"),
    ("network-core", "Network Core"),
    ("router-inter-reti", "Router e inter-reti"),
    ("commutazione-circuito", "Commutazione di circuito"),
    ("fdm", "FDM"),
    ("tdm", "TDM"),
    ("commutazione-pacchetto", "Commutazione di pacchetto"),
    ("store-and-forward", "Store-and-forward"),
    ("struttura-internet", "Struttura di Internet"),
    ("gerarchia-reti-isp", "Gerarchia reti e ISP"),
    ("isp-tier-1", "ISP Tier-1"),
    ("ixp", "Internet Exchange Point"),
    ("content-provider-networks", "Content Provider Networks"),
    ("prestazioni-rete", "Prestazioni di rete"),
    ("ritardi-nodali", "Ritardi nodali"),
    ("throughput", "Throughput"),
    ("bottleneck", "Bottleneck"),
    ("adsl", "ADSL"),
    ("dsl", "DSL"),
    ("fttx", "FTTx"),
    ("hfc", "HFC"),
    ("csma", "CSMA"),
    ("osi", "ISO/OSI"),
]

RULE_KEYWORDS: dict[str, list[str]] = {
    "tcp": ["tcp", "three-way", "window", "ack"],
    "udp": ["udp", "datagram"],
    "dns": ["dns", "domain name", "resolver"],
    "http": ["http", "https", "request", "response", "rest"],
    "smtp": ["smtp", "mail transfer"],
    "dhcp": ["dhcp", "lease"],
    "icmp": ["icmp", "ping"],
    "ipv4": ["ipv4", "32 bit"],
    "ipv6": ["ipv6", "128 bit"],
    "ethernet": ["ethernet", "mac address", "switch"],
    "routing": ["routing", "ospf", "rip", "bgp", "router"],
    "nat": ["nat", "masquerading"],
    "subnetting": ["subnet", "cidr", "netmask"],
    "arq": ["go-back-n", "stop-and-wait", "arq", "selective repeat"],
    "tls": ["tls", "ssl", "certificate", "handshake"],
    "firewall": ["firewall", "packet filtering"],
    "sicurezza": ["sicurezza", "security", "attacco", "mitm"],
    # Modulo 1 keywords
    "introduzione-reti": ["internet", "rete", "network", "protocollo"],
    "internet-ict-protocolli": ["ict", "protocollo", "protocol"],
    "architettura-livelli": ["livello", "layer", "architettura a livelli"],
    "pila-protocolli-internet": ["applicazione", "trasporto", "rete", "collegamento", "fisico", "stack"],
    "incapsulamento": ["incapsulamento", "segmento", "datagramma", "frame"],
    "network-edge": ["network edge", "periferia della rete"],
    "host-sistemi-periferici": ["host", "sistema periferico", "end system"],
    "reti-accesso": ["rete di accesso", "accesso residenziale", "accesso mobile"],
    "mezzi-trasmissivi": ["mezzo trasmissivo", "fibra", "rame", "wireless", "propagazione"],
    "network-core": ["network core", "nucleo della rete"],
    "router-inter-reti": ["router", "inter-rete", "internetwork"],
    "commutazione-circuito": ["commutazione di circuito", "circuit switching"],
    "fdm": ["fdm", "divisione di frequenza"],
    "tdm": ["tdm", "divisione di tempo"],
    "commutazione-pacchetto": ["commutazione di pacchetto", "packet switching"],
    "store-and-forward": ["store-and-forward", "store and forward"],
    "struttura-internet": ["struttura di internet", "internet structure"],
    "gerarchia-reti-isp": ["gerarchia", "isp", "provider"],
    "isp-tier-1": ["tier-1", "tier 1"],
    "ixp": ["ixp", "internet exchange point"],
    "content-provider-networks": ["content provider", "cdn", "google network"],
    "prestazioni-rete": ["prestazioni", "performance"],
    "ritardi-nodali": ["ritardo", "accodamento", "propagazione", "trasmissione", "elaborazione"],
    "throughput": ["throughput"],
    "bottleneck": ["bottleneck", "collo di bottiglia"],
    "adsl": ["adsl", "asymmetric digital subscriber line"],
    "dsl": ["dsl", "dslam", "splitter", "dsl modem", "linea telefonica"],
    "fttx": ["fttx", "ftth", "fttc", "fttb"],
    "hfc": ["hfc", "hybrid fiber coax"],
    "csma": ["csma", "carrier sense multiple access"],
    "osi": ["iso/osi", "osi", "livello applicazione", "livello trasporto", "livello rete", "livello collegamento"],
}

MODULE_1_PRESET_SLUG = "modulo-1"
MODULE_1_PRESET_NAME = "Modulo 1"
MODULE_1_PRESET_DESCRIPTION = (
    "Fondamenti reti: introduzione, architettura livelli, edge/core, switching, struttura Internet, prestazioni."
)
MODULE_1_TAG_SLUGS = [
    "introduzione-reti",
    "internet-ict-protocolli",
    "architettura-livelli",
    "pila-protocolli-internet",
    "incapsulamento",
    "network-edge",
    "host-sistemi-periferici",
    "reti-accesso",
    "mezzi-trasmissivi",
    "network-core",
    "router-inter-reti",
    "commutazione-circuito",
    "fdm",
    "tdm",
    "commutazione-pacchetto",
    "store-and-forward",
    "struttura-internet",
    "gerarchia-reti-isp",
    "isp-tier-1",
    "ixp",
    "content-provider-networks",
    "prestazioni-rete",
    "ritardi-nodali",
    "throughput",
    "bottleneck",
    "adsl",
    "dsl",
    "fttx",
    "hfc",
    "csma",
    "osi",
    # Compatibilita' con tag gia' presenti nel DB
    "reti",
    "tcp",
    "udp",
    "dns",
    "http",
    "smtp",
    "dhcp",
    "icmp",
    "ipv4",
    "ipv6",
    "ethernet",
    "routing",
    "nat",
    "subnetting",
    "arq",
]

INTERCORSO_1_PRESET_SLUG = "intercorso-1"
INTERCORSO_1_PRESET_NAME = "Intercorso 1"
INTERCORSO_1_PRESET_DESCRIPTION = (
    "Domande estratte dalla banca intercorso (flattened PDF) con tagging AI + post-processing."
)
INTERCORSO_1_BANK_PATH = Path(r"c:\Users\nextc\Examable\banca_domande_postprocessed.json")

MODULE_2_PRESET_SLUG = "modulo-2"
MODULE_2_PRESET_NAME = "Modulo 2"
MODULE_2_PRESET_DESCRIPTION = (
    "Domande dal secondo intercorso e dai temi d'esame passati (esame_*/traccia_*)."
)
MODULE_2_DOC_TITLE_SQL = r"^(esame|traccia)_.*\.pdf$"


def _slugify(value: str) -> str:
    s = unicodedata.normalize("NFKD", value or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "tag"


def _question_text(stem: str, options: Any, subparts: Any) -> str:
    parts = [stem or ""]
    if isinstance(options, list):
        parts.extend(str(o.get("text", "")) for o in options if isinstance(o, dict))
    if isinstance(subparts, list):
        parts.extend(str(s.get("prompt", "")) for s in subparts if isinstance(s, dict))
    return " ".join(parts).lower()


def _rule_suggest_tags(stem: str, options: Any, subparts: Any) -> list[tuple[str, float]]:
    text_blob = _question_text(stem, options, subparts)
    found: list[tuple[str, float]] = [("reti", 0.5)]
    for slug, keys in RULE_KEYWORDS.items():
        matches = sum(1 for k in keys if k in text_blob)
        if matches > 0:
            score = min(1.0, 0.55 + (matches * 0.15))
            found.append((slug, score))
    dedupe: dict[str, float] = {}
    for slug, score in found:
        dedupe[slug] = max(dedupe.get(slug, 0.0), score)
    return sorted(dedupe.items(), key=lambda x: x[1], reverse=True)


def _ai_suggest_tags(stem: str, options: Any, subparts: Any, allowed_slugs: list[str]) -> list[str]:
    if not settings.multimodal_enabled or not settings.multimodal_api_key:
        return []
    prompt = {
        "task": "Suggest tags for a network exam question.",
        "allowed_tags": allowed_slugs,
        "question": {
            "stem": stem,
            "options": options if isinstance(options, list) else [],
            "subparts": subparts if isinstance(subparts, list) else [],
        },
        "output": {"tags": ["allowed_tag_slug"]},
    }
    payload = {
        "model": settings.multimodal_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "Return strict JSON only with key `tags`, using only allowed tag slugs.",
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
    }
    url = settings.multimodal_api_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.multimodal_api_key}"}
    try:
        with httpx.Client(timeout=12.0) as client:
            response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        tags = parsed.get("tags", [])
        if not isinstance(tags, list):
            return []
        allowed = set(allowed_slugs)
        out = [str(t).strip() for t in tags if str(t).strip() in allowed]
        return list(dict.fromkeys(out))
    except Exception:
        return []


def ensure_base_tags(conn: Any) -> None:
    for slug, name in BASE_TAGS:
        conn.execute(
            text(
                """
                INSERT INTO tags (name, slug)
                VALUES (:name, :slug)
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                """
            ),
            {"name": name, "slug": slug},
        )


def ensure_tagging_schema(conn: Any) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tag_presets (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              name TEXT NOT NULL UNIQUE,
              slug TEXT NOT NULL UNIQUE,
              description TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tag_preset_tags (
              preset_id UUID NOT NULL REFERENCES tag_presets(id) ON DELETE CASCADE,
              tag_id UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
              PRIMARY KEY (preset_id, tag_id)
            )
            """
        )
    )


def ensure_module_1_preset(conn: Any) -> None:
    ensure_base_tags(conn)
    ensure_tagging_schema(conn)
    preset = conn.execute(
        text(
            """
            INSERT INTO tag_presets (name, slug, description)
            VALUES (:name, :slug, :description)
            ON CONFLICT (slug)
            DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description
            RETURNING id
            """
        ),
        {"name": MODULE_1_PRESET_NAME, "slug": MODULE_1_PRESET_SLUG, "description": MODULE_1_PRESET_DESCRIPTION},
    ).first()
    if not preset:
        return
    preset_id = str(preset.id)
    for tag_slug in MODULE_1_TAG_SLUGS:
        tag_id = _get_tag_id(conn, tag_slug)
        if not tag_id:
            continue
        conn.execute(
            text(
                """
                INSERT INTO tag_preset_tags (preset_id, tag_id)
                VALUES (:preset_id, :tag_id)
                ON CONFLICT (preset_id, tag_id) DO NOTHING
                """
            ),
            {"preset_id": preset_id, "tag_id": tag_id},
        )


def _ensure_preset(conn: Any, *, name: str, slug: str, description: str, tag_slugs: list[str]) -> None:
    preset = conn.execute(
        text(
            """
            INSERT INTO tag_presets (name, slug, description)
            VALUES (:name, :slug, :description)
            ON CONFLICT (slug)
            DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description
            RETURNING id
            """
        ),
        {"name": name, "slug": slug, "description": description},
    ).first()
    if not preset:
        return
    preset_id = str(preset.id)
    for tag_slug in sorted(set(tag_slugs)):
        tag_id = _get_tag_id(conn, tag_slug)
        if not tag_id:
            # create missing tags so the preset remains complete.
            conn.execute(
                text(
                    """
                    INSERT INTO tags (name, slug)
                    VALUES (:name, :slug)
                    ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                    """
                ),
                {"name": tag_slug.replace("-", " ").title(), "slug": tag_slug},
            )
            tag_id = _get_tag_id(conn, tag_slug)
        if not tag_id:
            continue
        conn.execute(
            text(
                """
                INSERT INTO tag_preset_tags (preset_id, tag_id)
                VALUES (:preset_id, :tag_id)
                ON CONFLICT (preset_id, tag_id) DO NOTHING
                """
            ),
            {"preset_id": preset_id, "tag_id": tag_id},
        )


def _intercorso_1_tag_slugs_from_bank() -> list[str]:
    if not INTERCORSO_1_BANK_PATH.exists():
        return MODULE_1_TAG_SLUGS
    try:
        payload = json.loads(INTERCORSO_1_BANK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return MODULE_1_TAG_SLUGS
    questions = payload.get("questions", [])
    if not isinstance(questions, list):
        return MODULE_1_TAG_SLUGS
    tags: set[str] = set()
    for q in questions:
        if not isinstance(q, dict):
            continue
        for t in q.get("tags", []):
            tag = _slugify(str(t))
            if tag:
                tags.add(tag)
    # Ensure core networking tags always included.
    tags.update(["reti", "throughput", "ritardi-nodali", "commutazione-pacchetto", "commutazione-circuito"])
    return sorted(tags) if tags else MODULE_1_TAG_SLUGS


def ensure_intercorso_1_preset(conn: Any) -> None:
    ensure_base_tags(conn)
    ensure_tagging_schema(conn)
    tag_slugs = _intercorso_1_tag_slugs_from_bank()
    _ensure_preset(
        conn,
        name=INTERCORSO_1_PRESET_NAME,
        slug=INTERCORSO_1_PRESET_SLUG,
        description=INTERCORSO_1_PRESET_DESCRIPTION,
        tag_slugs=tag_slugs,
    )


def _module_2_tag_slugs(conn: Any) -> list[str]:
    rows = conn.execute(
        text(
            """
            SELECT DISTINCT t.slug
            FROM question_tags qt
            JOIN tags t ON t.id = qt.tag_id
            JOIN questions q ON q.id = qt.question_id
            JOIN documents d ON d.id = q.document_id
            WHERE d.title = 'domande_seconda_intercorso.pdf'
               OR d.title ~* :title_pattern
            """
        ),
        {"title_pattern": MODULE_2_DOC_TITLE_SQL},
    ).all()
    return [r[0] for r in rows]


def ensure_module_2_preset(conn: Any) -> None:
    ensure_base_tags(conn)
    ensure_tagging_schema(conn)
    tag_slugs = _module_2_tag_slugs(conn)
    if not tag_slugs:
        return
    _ensure_preset(
        conn,
        name=MODULE_2_PRESET_NAME,
        slug=MODULE_2_PRESET_SLUG,
        description=MODULE_2_PRESET_DESCRIPTION,
        tag_slugs=tag_slugs,
    )


def _get_tag_id(conn: Any, slug_or_name: str) -> str | None:
    s = (slug_or_name or "").strip()
    if not s:
        return None
    row = conn.execute(
        text(
            """
            SELECT id
            FROM tags
            WHERE slug = :v OR name = :v OR CAST(id AS TEXT) = :v
            LIMIT 1
            """
        ),
        {"v": s},
    ).first()
    return str(row.id) if row else None


def set_question_tags(conn: Any, question_id: str, tags_with_score: list[tuple[str, float]], source: str) -> None:
    if not tags_with_score:
        return
    for slug, score in tags_with_score:
        tag_id = _get_tag_id(conn, slug)
        if tag_id is None:
            slug_norm = _slugify(slug)
            name = slug.replace("-", " ").title()
            conn.execute(
                text(
                    """
                    INSERT INTO tags (name, slug)
                    VALUES (:name, :slug)
                    ON CONFLICT (slug) DO NOTHING
                    """
                ),
                {"name": name, "slug": slug_norm},
            )
            tag_id = _get_tag_id(conn, slug_norm)
        if tag_id is None:
            continue
        conn.execute(
            text(
                """
                INSERT INTO question_tags (question_id, tag_id, score, source)
                VALUES (:question_id, :tag_id, :score, :source)
                ON CONFLICT (question_id, tag_id)
                DO UPDATE SET score = GREATEST(question_tags.score, EXCLUDED.score),
                              source = EXCLUDED.source
                """
            ),
            {"question_id": question_id, "tag_id": tag_id, "score": float(score), "source": source},
        )


def auto_tag_document(conn: Any, document_id: str, use_ai: bool = False) -> dict[str, int]:
    ensure_base_tags(conn)
    rows = conn.execute(
        text(
            """
            SELECT id, stem, options_json, subparts_json
            FROM questions
            WHERE document_id = :document_id
            """
        ),
        {"document_id": document_id},
    ).mappings()
    allowed = [slug for slug, _ in BASE_TAGS]
    tagged_count = 0
    ai_count = 0
    for row in rows:
        qid = str(row["id"])
        rule_tags = _rule_suggest_tags(str(row["stem"] or ""), row["options_json"], row["subparts_json"])
        set_question_tags(conn, qid, rule_tags, source="rule")
        if rule_tags:
            tagged_count += 1
        if use_ai:
            ai_tags = _ai_suggest_tags(
                str(row["stem"] or ""),
                row["options_json"],
                row["subparts_json"],
                allowed_slugs=allowed,
            )
            if ai_tags:
                set_question_tags(conn, qid, [(t, 0.82) for t in ai_tags], source="ai")
                ai_count += 1
    return {"questions_tagged": tagged_count, "questions_tagged_ai": ai_count}
