#!/usr/bin/env python3
"""Synthetic web corpus generator for Project Lantern (Builder 1: "web of data").

Generates ~2,500 interlinked HTML pages across 10 topics plus a small set of
``/admin/`` pages (used to exercise the crawler's robots-style disallow list).

Determinism
-----------
Every byte is derived from ``random.Random(seed)``. The topics are processed
in a fixed order, pages are assigned sequential ids, and all random draws
happen in a fixed sequence, so ``generate(seed=7, n=2500)`` is byte-identical
across runs and machines (manifest is serialized with ``sort_keys=True``).

Link structure (so PageRank is non-trivial)
------------------------------------------
* Each topic has 2 **hub** pages with 40-70 outlinks (mostly intra-topic).
* Regular pages link densely **within** their topic (3-6 intra-topic links +
  one link to a topic hub) and occasionally **across** topics (~8% of pages
  carry 1-2 cross-topic links).
* ~5% of content pages also link to an ``/admin/`` page; admin pages link
  among themselves and back to a few content pages.
* 1 **spotlight** page per topic carries a distinctive rare term
  (e.g. "sourdough hydration", "bebop scales") so search demos have clear
  winners.

Output
------
``<out_dir>/pages/p0001.html`` ... (content) and ``a0001.html`` ... (admin),
plus ``<out_dir>/manifest.json``: a list of ``{id, url, title, outlinks}``
with ``url`` of the form ``https://lantern.test/<topic>/<id>``.
"""

import html
import json
import random
import re
from pathlib import Path

BASE_URL = "https://lantern.test"

TOPIC_ORDER = [
    "python",
    "cooking",
    "space",
    "basketball",
    "gardening",
    "jazz",
    "hiking",
    "retro-gaming",
    "coffee",
    "cycling",
]

# Per-topic material: vocabulary, title templates, sentence templates,
# a rare "spotlight" term, and a one-line blurb used on hub pages.
# Sentence templates use {w} / {w2} placeholders filled from the vocabulary.
TOPICS = {
    "python": {
        "words": [
            "function", "variable", "loop", "decorator", "generator",
            "comprehension", "module", "package", "class", "inheritance",
            "exception", "traceback", "iterator", "lambda", "dictionary",
            "tuple", "slice", "pytest", "virtualenv", "pip", "asyncio",
            "thread", "socket", "regex", "dataclass", "typing", "import",
            "namespace", "closure", "recursion", "debugger", "profiler",
            "bytecode", "interpreter", "metaclass", "descriptor", "context",
            "manager", "fstring",
        ],
        "titles": [
            "Getting Started with {w} in Python",
            "A Practical Guide to Python {w}",
            "Understanding Python {w}",
            "{w} Explained with Examples",
            "Common Pitfalls with Python {w}",
            "How to Debug {w} in Python",
            "Python {w}: Tips and Tricks",
            "The Complete {w} Tutorial",
            "Python {w} for Beginners",
            "Advanced Patterns with {w}",
        ],
        "sents": [
            "The {w} is one of the most useful features in the language.",
            "Many beginners struggle with {w} at first, but practice helps.",
            "You can combine {w} with {w2} to write cleaner code.",
            "Understanding {w} will make your {w2} much more reliable.",
            "In this section we look at how {w} works under the hood.",
            "A common mistake is misusing {w} inside a {w2}.",
            "Experienced developers reach for {w} when {w2} gets complicated.",
            "Let us walk through a small example using {w} and {w2}.",
            "The official documentation has a thorough chapter on {w}.",
            "Once you master {w}, the rest of {w2} feels natural.",
        ],
        "rare": "cython nogil pragma",
        "rare_title": "Cython nogil Pragma: Releasing the GIL in Hot Loops",
        "blurb": "Practical Python programming tutorials, from first scripts to advanced patterns.",
    },
    "cooking": {
        "words": [
            "sourdough", "knead", "proof", "ferment", "skillet", "saute",
            "roast", "braise", "caramelize", "deglaze", "simmer", "blanch",
            "julienne", "mince", "zest", "marinade", "brine", "stock",
            "roux", "emulsion", "souffle", "pastry", "dough", "crumb",
            "crust", "gluten", "yeast", "starter", "cast", "iron", "knife",
            "mise", "seasoning", "herbs", "spices", "olive", "garlic",
            "onion", "tomato", "basil", "thyme", "rosemary",
        ],
        "titles": [
            "The Art of {w}",
            "How to Master {w} at Home",
            "{w}: A Beginner's Guide",
            "Perfect {w} Every Time",
            "The Science Behind {w}",
            "{w} Techniques from Professional Chefs",
            "Weeknight {w} Made Simple",
            "Classic {w} Recipes",
            "Troubleshooting Your {w}",
            "{w} for Home Cooks",
        ],
        "sents": [
            "Great {w} starts with quality ingredients and patience.",
            "Most home cooks under-season their {w}; do not be shy.",
            "The secret to good {w} is controlling heat and timing.",
            "Let the {w} rest before serving for the best {w2}.",
            "Professional chefs swear by this approach to {w}.",
            "A sharp knife makes {w} safer and faster.",
            "Taste your {w} as you go and adjust the {w2}.",
            "This method for {w} works with almost any {w2}.",
            "Do not overcrowd the pan when you {w}.",
            "A little acid at the end brightens any {w}.",
        ],
        "rare": "sourdough hydration",
        "rare_title": "Sourdough Hydration: A Complete Guide to Baker's Percentages",
        "blurb": "Recipes, techniques, and kitchen science for home cooks.",
    },
    "space": {
        "words": [
            "orbit", "gravity", "nebula", "galaxy", "quasar", "pulsar",
            "exoplanet", "telescope", "rocket", "satellite", "astronaut",
            "lander", "rover", "comet", "asteroid", "meteor", "eclipse",
            "solstice", "constellation", "lightyear", "redshift", "supernova",
            "black", "hole", "dwarf", "planet", "moon", "crater", "atmosphere",
            "magnetosphere", "spectroscopy", "parallax", "zenith", "nadir",
            "perigee", "equinox", "zenith", "cosmos",
        ],
        "titles": [
            "Exploring the {w}",
            "What Is a {w}?",
            "The Mystery of the {w}",
            "How {w} Shape the Universe",
            "Observing {w} from Earth",
            "Missions to Study {w}",
            "The Physics of {w}",
            "{w}: A Visual Guide",
            "Ten Facts About {w}",
            "The Future of {w} Research",
        ],
        "sents": [
            "The {w} has fascinated astronomers for centuries.",
            "Recent observations of the {w} changed our models.",
            "A {w} forms when matter collapses under its own {w2}.",
            "Spacecraft have visited the {w} up close.",
            "The {w} is larger than early estimates suggested.",
            "Amateur astronomers can spot the {w} with a small {w2}.",
            "Understanding the {w} helps explain the {w2}.",
            "Data from the latest survey refined our view of the {w}.",
            "The {w} will evolve dramatically over the next {w2}.",
            "No one has yet explained every feature of the {w}.",
        ],
        "rare": "aphelion transit",
        "rare_title": "Aphelion Transit: When Distant Worlds Pass at Their Farthest",
        "blurb": "Astronomy news, mission updates, and guides to the night sky.",
    },
    "basketball": {
        "words": [
            "dribble", "shoot", "pass", "rebound", "defense", "offense",
            "fast", "break", "pick", "roll", "screen", "cut", "post",
            "perimeter", "paint", "arc", "free", "throw", "foul", "timeout",
            "overtime", "playoff", "draft", "rookie", "veteran", "coach",
            "referee", "arena", "crowd", "buzzer", "beater", "alley",
            "oop", "crossover", "fadeaway", "hook", "shot", "layup",
            "dunk", "block", "steal", "assist", "turnover",
        ],
        "titles": [
            "Mastering the {w}",
            "{w} Drills for Every Level",
            "The Fundamentals of {w}",
            "How Pros Train Their {w}",
            "{w} Strategy Breakdown",
            "Improving Your {w} This Season",
            "The History of the {w}",
            "{w} Mistakes to Avoid",
            "Film Study: Great {w}",
            "Coaching {w} to Young Players",
        ],
        "sents": [
            "A reliable {w} separates good players from great ones.",
            "Repetition is the key to a consistent {w}.",
            "Coaches emphasize {w} in every practice session.",
            "Watch how the pros time their {w} off the {w2}.",
            "Footwork is the foundation of a strong {w}.",
            "The best teams execute their {w} without thinking.",
            "Film study reveals the details behind an elite {w}.",
            "Young players should master {w} before advanced {w2}.",
            "Conditioning supports every {w} late in games.",
            "A smart {w} creates easy looks for teammates.",
        ],
        "rare": "blitz pick-and-roll coverage",
        "rare_title": "Blitz Pick-and-Roll Coverage: Schemes, Rotations, and Counters",
        "blurb": "Training drills, strategy breakdowns, and film study for basketball players.",
    },
    "gardening": {
        "words": [
            "soil", "compost", "mulch", "seed", "seedling", "sprout",
            "harvest", "prune", "water", "sunlight", "shade", "bed",
            "raised", "container", "greenhouse", "perennial", "annual",
            "bulb", "tuber", "rhizome", "pollinator", "bee", "butterfly",
            "worm", "fertilizer", "nitrogen", "phosphorus", "potassium",
            "ph", "drainage", "aeration", "tilth", "cover", "crop", "rotate",
            "succession", "companion", "trellis", "stake", "twine",
        ],
        "titles": [
            "Growing with {w}",
            "The Gardener's Guide to {w}",
            "{w} for Small Spaces",
            "Seasonal {w} Calendar",
            "Organic {w} Methods",
            "Troubleshooting {w} Problems",
            "{w} on a Budget",
            "The Joy of {w}",
            "{w} for Beginners",
            "Advanced {w} Techniques",
        ],
        "sents": [
            "Healthy {w} is the foundation of a thriving garden.",
            "Start your {w} early indoors for a head start.",
            "Most vegetables need at least six hours of {w2} with good {w}.",
            "Water deeply but less often to encourage strong {w}.",
            "Rotate your {w} each year to prevent disease in the {w2}.",
            "A thick layer of {w} suppresses weeds around the {w2}.",
            "Observe your {w} daily and you will catch problems early.",
            "Native plants pair beautifully with traditional {w}.",
            "Feed the {w}, not just the plants, for lasting {w2}.",
            "Keep a garden journal to track what your {w} liked.",
        ],
        "rare": "hugelkultur mound",
        "rare_title": "Hugelkultur Mound: Building a Self-Watering Raised Bed",
        "blurb": "Soil, seeds, and seasonal know-how for productive gardens.",
    },
    "jazz": {
        "words": [
            "swing", "improvisation", "syncopation", "blue", "note",
            "chord", "progression", "voicing", "comping", "walking",
            "bass", "ride", "brush", "snare", "trumpet", "saxophone",
            "trombone", "piano", "guitar", "vibraphone", "quartet",
            "quintet", "big", "band", "combo", "standard", "head",
            "solo", "chorus", "bridge", "rhythm", "section", "groove",
            "feel", "tempo", "ballad", "uptempo", "modal", "fusion",
        ],
        "titles": [
            "The Language of {w}",
            "Learning {w} by Ear",
            "{w}: A Listener's Guide",
            "Practicing {w} Daily",
            "The History of {w}",
            "Great Recordings of {w}",
            "Understanding {w} Harmony",
            "{w} for New Players",
            "Transcribing Classic {w}",
            "The Feel Behind {w}",
        ],
        "sents": [
            "The {w} gives jazz its unmistakable pulse.",
            "Listen closely to how the masters phrase the {w}.",
            "A strong {w} frees the soloist to explore the {w2}.",
            "Transcribing {w} passages builds vocabulary fast.",
            "The {w} evolved from blues and ragtime roots.",
            "Practice {w} slowly before pushing the {w2}.",
            "Every combo needs a shared language for {w}.",
            "The greats made {w} sound effortless through the {w2}.",
            "Feel matters more than speed when playing {w}.",
            "Record yourself to hear your {w} honestly.",
        ],
        "rare": "bebop scales",
        "rare_title": "Bebop Scales: Adding Chromatic Passing Tones to Your Lines",
        "blurb": "Listening guides, practice routines, and histories from the jazz world.",
    },
    "hiking": {
        "words": [
            "trail", "summit", "ridge", "valley", "switchback", "cairn",
            "blazes", "map", "compass", "gps", "backpack", "tent",
            "sleeping", "bag", "stove", "filter", "bottle", "boots",
            "socks", "layers", "rain", "shell", "trekking", "poles",
            "headlamp", "first", "aid", "bear", "canister", "leave",
            "trace", "campsite", "waterfall", "meadow", "alpine",
            "treeline", "scramble", "traverse", "descent",
        ],
        "titles": [
            "Hiking the {w}",
            "Essential {w} Skills",
            "Planning a {w} Trip",
            "The Best {w} Routes",
            "{w} Safety Basics",
            "Packing for {w}",
            "{w} in Every Season",
            "Reading {w} Like a Pro",
            "Solo {w} Tips",
            "Family {w} Adventures",
        ],
        "sents": [
            "Check conditions before heading out on the {w}.",
            "A good {w} starts with honest trip planning.",
            "Pack the ten essentials for any {w}, even a short {w2}.",
            "Weather above the {w} can change in minutes.",
            "Leave an itinerary with someone before a remote {w}.",
            "Pace yourself on the {w} and save energy for the {w2}.",
            "The {w} rewards early starts and steady legs.",
            "Practice with your {w} close to home first.",
            "Respect closures that protect a fragile {w}.",
            "Every {w} teaches you something new about the {w2}.",
        ],
        "rare": "ultralight base weight",
        "rare_title": "Ultralight Base Weight: Cutting Your Pack Below Five Kilograms",
        "blurb": "Trail guides, gear lists, and backcountry skills for hikers.",
    },
    "retro-gaming": {
        "words": [
            "sprite", "pixel", "palette", "tilemap", "parallax", "scroll",
            "cartridge", "console", "arcade", "cabinet", "joystick",
            "dpad", "crt", "scanline", "emulator", "rom", "homebrew",
            "speedrun", "high", "score", "boss", "level", "powerup",
            "continue", "checkpoint", "side", "scroll", "platform",
            "shooter", "rpg", "puzzle", "chiptune", "soundtrack",
            "composer", "developer", "port", "remake", "demake",
        ],
        "titles": [
            "The Golden Age of {w}",
            "Why {w} Still Matters",
            "Collecting {w} Classics",
            "The Tech Behind {w}",
            "{w} Design Secrets",
            "Restoring {w} Hardware",
            "The Music of {w}",
            "{w} Speedrunning Guide",
            "Hidden Gems of {w}",
            "Emulating {w} Faithfully",
        ],
        "sents": [
            "The {w} defined a generation of players.",
            "Developers squeezed miracles out of the {w}.",
            "A great {w} still feels tight decades later.",
            "Collectors prize complete copies with the {w2}.",
            "The {w} rewarded patience and pattern recognition.",
            "Chiptune composers turned limits of the {w} into art.",
            "Speedrunners keep pushing the {w} to its limits.",
            "Preserving the {w} means preserving the {w2} too.",
            "Modern indies borrow heavily from classic {w}.",
            "Playing on original hardware changes how the {w} feels.",
        ],
        "rare": "scanline mask shaders",
        "rare_title": "Scanline Mask Shaders: Faithful CRT Emulation Explained",
        "blurb": "Hardware, history, and high scores from the retro gaming scene.",
    },
    "coffee": {
        "words": [
            "espresso", "pour", "over", "french", "press", "aeropress",
            "chemex", "v60", "grind", "burr", "dose", "yield", "ratio",
            "bloom", "extraction", "channeling", "crema", "body",
            "acidity", "sweetness", "aroma", "roast", "light", "medium",
            "dark", "single", "origin", "blend", "washed", "natural",
            "honey", "process", "altitude", "varietal", "freshness",
            "degas", "rest", "kettle", "scale", "timer",
        ],
        "titles": [
            "Brewing Better {w}",
            "The Craft of {w}",
            "{w} at Home",
            "Understanding {w}",
            "Dialing In Your {w}",
            "The Science of {w}",
            "{w} Gear Guide",
            "Tasting {w} Like a Pro",
            "Common {w} Mistakes",
            "The Ritual of {w}",
        ],
        "sents": [
            "Fresh beans make the biggest difference in your {w}.",
            "Grind size controls {w} more than any other variable.",
            "A scale turns guesswork into repeatable {w}.",
            "Water temperature shapes the {w} of every {w2}.",
            "Let the coffee bloom before continuing the {w}.",
            "Taste your {w} black to judge the {w2} honestly.",
            "Small changes in {w} compound into big flavor shifts.",
            "Clean equipment is the cheapest upgrade to {w}.",
            "Rest freshly roasted beans before brewing {w}.",
            "Keep notes so your best {w} is repeatable.",
        ],
        "rare": "bloom degassing",
        "rare_title": "Bloom Degassing: Why Fresh Coffee Needs to Breathe First",
        "blurb": "Brew methods, gear, and tasting notes for coffee lovers.",
    },
    "cycling": {
        "words": [
            "cadence", "gear", "chain", "derailleur", "cassette",
            "crankset", "wheel", "tire", "tube", "tubeless", "brake",
            "disc", "rim", "frame", "carbon", "alloy", "saddle",
            "handlebar", "pedal", "cleat", "kit", "helmet", "draft",
            "peloton", "breakaway", "sprint", "climb", "descent",
            "time", "trial", "interval", "zone", "ftp", "power",
            "heart", "rate", "recovery", "bonk", "nutrition",
        ],
        "titles": [
            "Riding Stronger with {w}",
            "The Cyclist's Guide to {w}",
            "{w} Basics",
            "Training Your {w}",
            "Choosing the Right {w}",
            "Maintaining Your {w}",
            "{w} for Commuters",
            "Racing with {w}",
            "The Physics of {w}",
            "Winter {w} Training",
        ],
        "sents": [
            "A smooth {w} saves energy on long rides.",
            "Fit matters more than price when choosing a {w}.",
            "Structured work on {w} builds fitness faster than junk miles.",
            "Keep your {w} clean and it will last for years.",
            "Learn to read the {w} before you need to fix the {w2}.",
            "Fuel early and often to protect your {w} on the {w2}.",
            "Group rides teach {w} faster than solo miles.",
            "A professional bike fit transforms your {w}.",
            "Consistency beats intensity for building {w}.",
            "Respect the weather when planning around {w}.",
        ],
        "rare": "cadence zone drills",
        "rare_title": "Cadence Zone Drills: Building an Efficient Pedal Stroke",
        "blurb": "Training plans, maintenance tips, and route ideas for cyclists.",
    },
}

# Shared generic sentence frames (use {w} / {w2}).
GENERIC_SENTS = [
    "This guide covers everything you need to know about {w}.",
    "Readers often ask about {w2} after learning {w}.",
    "Bookmark this page as a reference for {w}.",
    "We update this article whenever {w} best practices change.",
    "Share your own experience with {w} in the comments.",
    "Next, we will look at how {w} connects to {w2}.",
]

ADMIN_TITLES = [
    "Lantern Admin: Server Metrics",
    "Lantern Admin: Crawl Queue Monitor",
    "Lantern Admin: Index Shard Status",
    "Lantern Admin: Query Log Viewer",
    "Lantern Admin: Cache Statistics",
    "Lantern Admin: Crawler Politeness Settings",
    "Lantern Admin: Backup Scheduler",
    "Lantern Admin: User Accounts",
    "Lantern Admin: API Token Manager",
    "Lantern Admin: Disk Usage Report",
    "Lantern Admin: Error Dashboard",
    "Lantern Admin: Deployment Checklist",
]

ADMIN_SENTS = [
    "This internal page shows {w} for the lantern cluster.",
    "Only operators should access the {w} panel.",
    "The {w} refreshes every sixty seconds.",
    "Use the {w} to diagnose slow queries and stuck {w2}.",
    "Credentials for the {w} are managed by the on-call {w2}.",
]
ADMIN_WORDS = [
    "dashboard", "metrics", "latency", "throughput", "shard", "replica",
    "queue", "worker", "token", "audit", "log", "alert", "uptime",
    "deploy", "rollback", "backup", "restore", "quota", "throttle",
]


def _norm_id(letter, num):
    return "%s%04d" % (letter, num)


def generate(seed=7, n=2500, out_dir="corpus"):
    """Generate the synthetic corpus.

    Args:
        seed: RNG seed; same seed -> byte-identical output.
        n: number of content pages (admin pages are extra, ``n // 100``).
        out_dir: output directory; pages go to ``<out_dir>/pages/``,
            manifest to ``<out_dir>/manifest.json``.

    Returns:
        dict with keys ``pages``, ``admin``, ``manifest`` (path str).
    """
    rng = random.Random(seed)
    out_dir = Path(out_dir)
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    topics = TOPIC_ORDER
    tcount = len(topics)

    # --- Phase 1: assign ids and titles -----------------------------------
    base, rem = divmod(n, tcount)
    counts = [base + (1 if i < rem else 0) for i in range(tcount)]

    pages = []  # dicts: id, topic, url, title, kind
    pid = 1
    for ti, topic in enumerate(topics):
        for j in range(counts[ti]):
            ident = _norm_id("p", pid)
            pid += 1
            kind = "hub" if j < 2 else ("spotlight" if j == 2 else "regular")
            if rng.random() < 0.15 and kind == "regular":
                kind = "thin"
            pages.append({"id": ident, "topic": topic,
                          "url": "%s/%s/%s" % (BASE_URL, topic, ident),
                          "kind": kind, "idx": j})
    n_admin = n // 100
    admin_ids = []
    for k in range(n_admin):
        ident = _norm_id("a", k + 1)
        admin_ids.append(ident)
        pages.append({"id": ident, "topic": "admin",
                      "url": "%s/admin/%s" % (BASE_URL, ident),
                      "kind": "admin", "idx": k})

    by_id = {p["id"]: p for p in pages}
    by_topic = {t: [p["id"] for p in pages if p["topic"] == t] for t in topics}

    for p in pages:
        if p["kind"] == "admin":
            p["title"] = ADMIN_TITLES[p["idx"] % len(ADMIN_TITLES)]
        elif p["kind"] == "hub":
            p["title"] = "The Ultimate %s Hub: Guides, Tips, and Resources" % (
                p["topic"].replace("-", " ").title())
        elif p["kind"] == "spotlight":
            p["title"] = TOPICS[p["topic"]]["rare_title"]
        else:
            data = TOPICS[p["topic"]]
            tmpl = rng.choice(data["titles"])
            w = rng.choice(data["words"])
            p["title"] = tmpl.format(w=w).replace("  ", " ").strip()

    # --- Phase 2: link graph ----------------------------------------------
    outlinks = {p["id"]: [] for p in pages}

    def add_link(src, dst):
        if dst != src and dst not in outlinks[src]:
            outlinks[src].append(dst)

    for p in pages:
        src = p["id"]
        topic = p["topic"]
        if topic == "admin":
            # Admin pages: dense among themselves, a few links to content.
            for _ in range(rng.randint(3, 6)):
                add_link(src, rng.choice(admin_ids))
            content_ids = [q["id"] for q in pages if q["topic"] != "admin"]
            for _ in range(rng.randint(1, 2)):
                add_link(src, rng.choice(content_ids))
            continue
        peers = by_topic[topic]
        peers_wo_self = [x for x in peers if x != src]
        hubs = peers[:2]
        if p["kind"] == "hub":
            # Hubs: many outlinks; on small corpora link to (nearly) all peers.
            k = min(rng.randint(40, 70), len(peers_wo_self))
            for tid in rng.sample(peers_wo_self, k):
                add_link(src, tid)
            for _ in range(rng.randint(3, 6)):
                other = rng.choice([t for t in topics if t != topic])
                add_link(src, rng.choice(by_topic[other]))
        else:
            # one link to a topic hub, plus dense intra-topic links
            add_link(src, rng.choice(hubs))
            n_intra = rng.randint(2, 3) if p["kind"] == "thin" else rng.randint(3, 6)
            for _ in range(n_intra):
                add_link(src, rng.choice(peers))
            if rng.random() < 0.08:
                for _ in range(rng.randint(1, 2)):
                    other = rng.choice([t for t in topics if t != topic])
                    add_link(src, rng.choice(by_topic[other]))
            if admin_ids and rng.random() < 0.05:
                add_link(src, rng.choice(admin_ids))

    # --- Repair pass: every content page is directly linked from a topic hub,
    # so the whole corpus is reachable from the default hub seeds.
    for topic in topics:
        hubs = by_topic[topic][:2]
        hub_targets = set()
        for h in hubs:
            hub_targets.update(outlinks[h])
        for pid in by_topic[topic]:
            if pid not in hub_targets:
                hub = rng.choice(hubs)
                add_link(hub, pid)
                hub_targets.add(pid)

    # --- Phase 3: bodies + files ------------------------------------------
    for p in pages:
        targets = outlinks[p["id"]]
        body = _render_page(rng, p, targets, by_id)
        (pages_dir / (p["id"] + ".html")).write_text(body, encoding="utf-8")

    manifest = [
        {"id": p["id"], "url": p["url"], "title": p["title"],
         "outlinks": outlinks[p["id"]]}
        for p in pages
    ]
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return {"pages": n, "admin": n_admin, "manifest": str(manifest_path)}


def _sent(rng, topic, data):
    tmpl = rng.choice(data["sents"] + GENERIC_SENTS)
    w = rng.choice(data["words"])
    w2 = rng.choice(data["words"])
    return tmpl.format(w=w, w2=w2)


def _para(rng, topic, data, rare_boost=False):
    n = rng.randint(3, 7)
    sents = []
    for _ in range(n):
        if rare_boost and rng.random() < 0.35:
            sents.append(
                "Understanding %s is essential for anyone serious about %s."
                % (data["rare"], rng.choice(data["words"])))
        else:
            sents.append(_sent(rng, topic, data))
    return " ".join(sents)


def _render_page(rng, page, targets, by_id):
    topic = page["topic"]
    title = page["title"]
    esc = html.escape

    if topic == "admin":
        data = {"words": ADMIN_WORDS, "sents": ADMIN_SENTS}
        paras = [" ".join(
            rng.choice(ADMIN_SENTS).format(
                w=rng.choice(ADMIN_WORDS), w2=rng.choice(ADMIN_WORDS))
            for _ in range(rng.randint(3, 5)))
            for _ in range(rng.randint(2, 4))]
        blurb = "Internal operations dashboard. Not for public indexing."
    else:
        data = TOPICS[topic]
        kind = page["kind"]
        if kind == "hub":
            n_para = rng.randint(5, 7)
        elif kind == "spotlight":
            n_para = 5
        elif kind == "thin":
            n_para = 2
        else:
            n_para = rng.randint(3, 6)
        paras = [_para(rng, topic, data, rare_boost=(kind == "spotlight"))
                 for _ in range(n_para)]
        blurb = data["blurb"]

    # Related-links list (every outlink appears here).
    items = []
    for tid in targets:
        anchor = esc(by_id[tid]["title"][:70])
        items.append('      <li><a href="%s">%s</a></li>' % (tid, anchor))
    related = "\n".join(items)

    # Inline link in the first paragraph for realism (first outlink only).
    if targets:
        anchor = esc(by_id[targets[0]]["title"][:60])
        paras[0] += ' See also <a href="%s">%s</a>.' % (targets[0], anchor)

    paras_html = "\n".join("    <p>%s</p>" % esc(p) for p in paras)
    # Un-escape the inline <a> we injected after escaping (rebuild it cleanly).
    # (We escaped the paragraph text above, so re-insert the anchor properly.)
    if targets:
        anchor = esc(by_id[targets[0]]["title"][:60])
        link_html = ' See also <a href="%s">%s</a>.' % (targets[0], anchor)
        paras_html = paras_html.replace(esc(link_html), link_html, 1)

    h2 = esc("About %s" % topic.replace("-", " ")) if topic != "admin" else esc("Operations")
    track = "%08x" % rng.randrange(16 ** 8)

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>%s</title>
<meta name="description" content="%s">
<style>
  body { font-family: sans-serif; max-width: 46em; margin: 2em auto; }
  nav a { margin-right: 1em; }
</style>
<script>
  var __trk_%s = 1; /* lantern analytics beacon */
  function track() { return __trk_%s; }
</script>
</head>
<body>
<header>
  <nav>
    <a href="%s">Home</a>
  </nav>
</header>
<main>
  <h1>%s</h1>
  <p class="blurb">%s</p>
  <h2>%s</h2>
%s
  <h2>Related pages</h2>
  <ul>
%s
  </ul>
</main>
<footer>
  <p>Lantern test corpus. Generated synthetically for search-engine experiments.</p>
</footer>
</body>
</html>
""" % (esc(title), esc(blurb[:150]), track, track,
       targets[0] if targets else "#",
       esc(title), esc(blurb), h2, paras_html, related)


if __name__ == "__main__":
    import sys
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 2500
    out = sys.argv[3] if len(sys.argv) > 3 else "corpus"
    res = generate(seed=seed, n=n, out_dir=out)
    print("wrote %d content + %d admin pages -> %s"
          % (res["pages"], res["admin"], res["manifest"]))
