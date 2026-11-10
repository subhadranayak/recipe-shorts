"""Short on-screen hook titles ("curiosity lines") for each clip.

Priority: user-supplied title > video title (filename or YouTube title) >
dish name guessed from the transcript > generic line. Everything here is
plain templates - no AI service needed.
"""
import re

GENERIC = {
    "hook": ["You need to try this at home", "Wait till you see the end", "Made in one pan"],
    "action": ["Wait for the sizzle", "This is where the magic happens", "The secret is in this step"],
    "payoff": ["The final toss is everything", "Straight from the pan", "Worth every minute"],
    "extra": ["Watch this", "Home-style, no shortcuts", "Simple but so good"],
}

ID_LIKE = re.compile(r"^[A-Za-z0-9_-]{11}(_\w+)?$")  # YouTube video ids etc.


def clean_title(name):
    """Turn a filename / video title into readable text, or '' if it looks like an id."""
    stem = re.sub(r"\.(mp4|mov|webm|mkv|m4v)$", "", name or "", flags=re.I)
    stem = re.sub(r"_(\d{3,4}p|fixed|final|v\d+)$", "", stem, flags=re.I)
    if not stem or ID_LIKE.match(stem) and " " not in stem and stem.count("_") < 2:
        return ""
    return re.sub(r"[_]+", " ", stem).strip()


def split_title(title):
    """'Gobi 65 Recipe | This Is What X Was Missing!' -> ('Gobi 65', 'This Is What X Was Missing!')."""
    parts = [p.strip() for p in re.split(r"\s[|\-–:]\s|\|", title) if p.strip()]
    dish = parts[0] if parts else title
    dish = re.sub(r"\b(recipe|recipes|making|how to make|easy|homemade|delicious|tasty)\b", "", dish,
                  flags=re.I)
    dish = re.sub(r"\s+", " ", dish).strip(" -|")
    tagline = parts[1] if len(parts) > 1 else ""
    # If the dish part was descriptive ("Delicious X Making") keep it short.
    words = dish.split()
    if len(words) > 4:
        dish = " ".join(words[-4:])
    return dish, tagline


def dish_from_transcript(transcript):
    for seg in transcript or []:
        m = re.search(r"\b(?:make|making|cook|cooking|prepare)\s+(?:a |an |the |some )?(.{3,40}?)(?:[.,!?]|$)",
                      seg["text"], flags=re.I)
        if m:
            words = re.sub(r"\b(today|very|crazy|and|style|street|delicious|tasty)\b", "", m.group(1),
                           flags=re.I).split()
            if words:
                return " ".join(words[-3:])
    return ""


def make_hooks(slot, title="", transcript=None, clip_text=""):
    """Return (hook, alternatives) for a clip slot."""
    dish, tagline = split_title(title) if title else ("", "")
    if not dish:
        dish = dish_from_transcript(transcript)
    dish = dish.title() if dish and dish.islower() else dish
    low = clip_text.lower()
    opts = []
    if slot == "hook":
        if tagline:
            opts.append(tagline)
        if dish:
            opts += [f"You've never had {dish} like this", f"{dish}, the easy way"]
    elif slot == "action":
        if dish and re.search(r"\b(fry|frying|oil|sizzl\w*)\b", low):
            opts.append(f"Watch this {dish} hit the oil")
        if re.search(r"\b(add|adding|paste|sauce|masala)\b", low):
            opts.append("The secret is in this step")
        if dish:
            opts.append(f"How {dish} really comes together")
    elif slot == "payoff":
        if dish:
            opts += [f"{dish}, straight from the pan", f"{dish} is ready!"]
        if re.search(r"\b(taste|bite)\b", low):
            opts.append("Wait for the first bite")
    opts += GENERIC.get(slot, GENERIC["extra"])
    # De-duplicate, keep phone-friendly length.
    seen, out = set(), []
    for o in opts:
        o = o.strip()
        if o and o.lower() not in seen and len(o) <= 48:
            seen.add(o.lower())
            out.append(o)
    return out[0], out[1:4]
