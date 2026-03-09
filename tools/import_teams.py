#!/usr/bin/env python3
"""
GameplayFootball - Real-time team data importer from football-data.org for the game's database.
"""

import argparse
import glob
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

import requests

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False
    print("WARNING: Pillow not installed. Kits will use default white template.")
    print("         Install with: pip3 install Pillow")

API_BASE = "https://api.football-data.org/v4"
RATE_LIMIT_DELAY = 6.5

# League codes mapped to country and display name
LEAGUE_INFO = {
    "PL":  {"country": "England",     "name": "Premier League"},
    "BL1": {"country": "Germany",     "name": "Bundesliga"},
    "PD":  {"country": "Spain",       "name": "La Liga"},
    "SA":  {"country": "Italy",       "name": "Serie A"},
    "FL1": {"country": "France",      "name": "Ligue 1"},
    "DED": {"country": "Netherlands", "name": "Eredivisie"},
    "PPL": {"country": "Portugal",    "name": "Primeira Liga"},
    "ELC": {"country": "England",     "name": "Championship"},
    "BSA": {"country": "Brazil",      "name": "Brasileirão"},
}

# Region mapping for countries
COUNTRY_REGIONS = {
    "England": "Europe", "Germany": "Europe", "Spain": "Europe",
    "Italy": "Europe", "France": "Europe", "Netherlands": "Europe",
    "Portugal": "Europe", "Brazil": "South America",
}

# Formation Templates (11 players)
# Each formation is a list of (x, y, role) tuples
# x: -1 (own goal) to 1 (opponent goal)
# y: -1 (right) to 1 (left)

FORMATIONS = {
    "4-4-2": [
        (-1.0,  0.0,  "GK"),
        (-0.7,  0.75, "LB"),
        (-1.0,  0.25, "CB"),
        (-1.0, -0.25, "CB"),
        (-0.7, -0.75, "RB"),
        (-0.2,  0.3,  "CM"),
        (-0.2, -0.3,  "CM"),
        ( 0.7,  0.9,  "LM"),
        ( 0.7, -0.9,  "RM"),
        ( 0.8,  0.3,  "CF"),
        ( 0.8, -0.3,  "CF"),
    ],
    "4-3-3": [
        (-1.0,  0.0,  "GK"),
        (-0.7,  0.75, "LB"),
        (-1.0,  0.25, "CB"),
        (-1.0, -0.25, "CB"),
        (-0.7, -0.75, "RB"),
        (-0.4,  0.0,  "DM"),
        (-0.1,  0.4,  "CM"),
        (-0.1, -0.4,  "CM"),
        ( 0.8,  0.8,  "LM"),
        ( 1.0,  0.0,  "CF"),
        ( 0.8, -0.8,  "RM"),
    ],
    "4-2-3-1": [
        (-1.0,  0.0,  "GK"),
        (-0.7,  0.75, "LB"),
        (-1.0,  0.25, "CB"),
        (-1.0, -0.25, "CB"),
        (-0.7, -0.75, "RB"),
        (-0.3,  0.2,  "DM"),
        (-0.3, -0.2,  "DM"),
        ( 0.5,  0.8,  "LM"),
        ( 0.2,  0.0,  "AM"),
        ( 0.5, -0.8,  "RM"),
        ( 1.0,  0.0,  "CF"),
    ],
    "3-5-2": [
        (-1.0,  0.0,  "GK"),
        (-0.9,  0.4,  "CB"),
        (-1.0,  0.0,  "CB"),
        (-0.9, -0.4,  "CB"),
        (-0.3,  0.85, "LM"),
        (-0.3, -0.85, "RM"),
        (-0.2,  0.0,  "DM"),
        ( 0.1,  0.3,  "CM"),
        ( 0.1, -0.3,  "CM"),
        ( 0.8,  0.3,  "CF"),
        ( 0.8, -0.3,  "CF"),
    ],
}

# Player Stat Profiles
# profile_xml stat distributions per position type.
# All values should average ~0.5 (as per modding.txt)

STAT_NAMES = [
    "physical_balance", "physical_reaction", "physical_acceleration",
    "physical_velocity", "physical_stamina", "physical_agility",
    "physical_shotpower", "technical_standingtackle", "technical_slidingtackle",
    "technical_ballcontrol", "technical_dribble", "technical_shortpass",
    "technical_highpass", "technical_header", "technical_shot",
    "technical_volley", "mental_calmness", "mental_workrate",
    "mental_resilience", "mental_defensivepositioning",
    "mental_offensivepositioning", "mental_vision",
]

# Base profiles per position type (values are relative distribution)
POSITION_PROFILES = {
    "GK": {
        "physical_balance": 0.55, "physical_reaction": 0.75, "physical_acceleration": 0.40,
        "physical_velocity": 0.30, "physical_stamina": 0.40, "physical_agility": 0.65,
        "physical_shotpower": 0.50, "technical_standingtackle": 0.20, "technical_slidingtackle": 0.15,
        "technical_ballcontrol": 0.35, "technical_dribble": 0.20, "technical_shortpass": 0.40,
        "technical_highpass": 0.55, "technical_header": 0.30, "technical_shot": 0.20,
        "technical_volley": 0.25, "mental_calmness": 0.70, "mental_workrate": 0.45,
        "mental_resilience": 0.65, "mental_defensivepositioning": 0.70,
        "mental_offensivepositioning": 0.15, "mental_vision": 0.45,
    },
    "DEF": {
        "physical_balance": 0.60, "physical_reaction": 0.55, "physical_acceleration": 0.45,
        "physical_velocity": 0.45, "physical_stamina": 0.55, "physical_agility": 0.40,
        "physical_shotpower": 0.40, "technical_standingtackle": 0.70, "technical_slidingtackle": 0.65,
        "technical_ballcontrol": 0.40, "technical_dribble": 0.30, "technical_shortpass": 0.50,
        "technical_highpass": 0.55, "technical_header": 0.65, "technical_shot": 0.25,
        "technical_volley": 0.25, "mental_calmness": 0.55, "mental_workrate": 0.60,
        "mental_resilience": 0.65, "mental_defensivepositioning": 0.75,
        "mental_offensivepositioning": 0.20, "mental_vision": 0.40,
    },
    "MID": {
        "physical_balance": 0.50, "physical_reaction": 0.55, "physical_acceleration": 0.55,
        "physical_velocity": 0.50, "physical_stamina": 0.65, "physical_agility": 0.55,
        "physical_shotpower": 0.55, "technical_standingtackle": 0.45, "technical_slidingtackle": 0.35,
        "technical_ballcontrol": 0.60, "technical_dribble": 0.55, "technical_shortpass": 0.65,
        "technical_highpass": 0.55, "technical_header": 0.40, "technical_shot": 0.50,
        "technical_volley": 0.45, "mental_calmness": 0.55, "mental_workrate": 0.60,
        "mental_resilience": 0.50, "mental_defensivepositioning": 0.45,
        "mental_offensivepositioning": 0.55, "mental_vision": 0.60,
    },
    "ATT": {
        "physical_balance": 0.45, "physical_reaction": 0.55, "physical_acceleration": 0.65,
        "physical_velocity": 0.65, "physical_stamina": 0.50, "physical_agility": 0.65,
        "physical_shotpower": 0.70, "technical_standingtackle": 0.20, "technical_slidingtackle": 0.15,
        "technical_ballcontrol": 0.60, "technical_dribble": 0.70, "technical_shortpass": 0.55,
        "technical_highpass": 0.45, "technical_header": 0.50, "technical_shot": 0.75,
        "technical_volley": 0.55, "mental_calmness": 0.50, "mental_workrate": 0.45,
        "mental_resilience": 0.45, "mental_defensivepositioning": 0.20,
        "mental_offensivepositioning": 0.75, "mental_vision": 0.50,
    },
}

# Hairstyles and colors available in the game
HAIRSTYLES = ["short01", "short02", "medium01", "bald"]
HAIR_COLORS = ["blonde", "darkblonde", "black", "brown"]

# Default Tactics XML

DEFAULT_TACTICS = """<dribble_centermagnet>{dcm:.6f}</dribble_centermagnet>
<dribble_offensiveness>{do:.6f}</dribble_offensiveness>
<position_defense_depth_factor>{pddf:.6f}</position_defense_depth_factor>
<position_defense_microfocus_strength>{pdms:.6f}</position_defense_microfocus_strength>
<position_defense_midfieldfocus>{pdmf:.6f}</position_defense_midfieldfocus>
<position_defense_sidefocus_strength>{pdss:.6f}</position_defense_sidefocus_strength>
<position_defense_width_factor>{pdwf:.6f}</position_defense_width_factor>
<position_offense_depth_factor>{podf:.6f}</position_offense_depth_factor>
<position_offense_microfocus_strength>{poms:.6f}</position_offense_microfocus_strength>
<position_offense_midfieldfocus>{pomf:.6f}</position_offense_midfieldfocus>
<position_offense_sidefocus_strength>{poss:.6f}</position_offense_sidefocus_strength>
<position_offense_width_factor>{powf:.6f}</position_offense_width_factor>
"""


# Helper Functions

def remove_accents(text: str) -> str:
    """Remove accents from unicode text for safe display."""
    nfkd = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def sanitize_dirname(name: str) -> str:
    """Create a safe directory name from a team/league name."""
    name = remove_accents(name).lower()
    name = re.sub(r'[^a-z0-9]', '', name)
    return name


def parse_club_colors(color_string: str):
    """Parse API 'clubColors' string like 'Red / White' into two RGB tuples."""
    COLOR_MAP = {
        "red": (255, 50, 50), "blue": (50, 50, 255), "white": (255, 255, 255),
        "black": (0, 0, 0), "yellow": (255, 255, 50), "green": (50, 200, 50),
        "orange": (255, 165, 0), "purple": (128, 0, 128), "gold": (255, 215, 0),
        "silver": (192, 192, 192), "navy": (0, 0, 128), "maroon": (128, 0, 0),
        "sky blue": (135, 206, 235), "claret": (128, 0, 32), "amber": (255, 191, 0),
        "crimson": (220, 20, 60), "scarlet": (255, 36, 0), "royal blue": (65, 105, 225),
        "light blue": (173, 216, 230), "dark blue": (0, 0, 139),
        "burgundy": (128, 0, 32), "pink": (255, 105, 180), "grey": (128, 128, 128),
        "gray": (128, 128, 128), "brown": (139, 69, 19), "violet": (127, 0, 255),
        "turquoise": (64, 224, 208), "teal": (0, 128, 128), "lime": (0, 255, 0),
        "coral": (255, 127, 80), "cyan": (0, 255, 255),
    }

    if not color_string:
        return (255, 50, 50), (255, 255, 255)

    parts = [p.strip().lower() for p in color_string.split("/")]
    colors = []
    for part in parts:
        matched = False
        for name, rgb in COLOR_MAP.items():
            if name in part:
                colors.append(rgb)
                matched = True
                break
        if not matched:
            colors.append((128, 128, 128))

    c1 = colors[0] if len(colors) > 0 else (255, 50, 50)
    c2 = colors[1] if len(colors) > 1 else (255, 255, 255)
    return c1, c2


def color_to_db(rgb: tuple) -> str:
    """Convert RGB tuple to database color string format."""
    return f"{rgb[0]}, {rgb[1]}, {rgb[2]}"


def api_position_to_game_category(position: str) -> str:
    """Map API position to game stat profile category."""
    pos = (position or "").lower()
    if "goal" in pos:
        return "GK"
    elif "defence" in pos or "back" in pos:
        return "DEF"
    elif "midfield" in pos:
        return "MID"
    elif "offence" in pos or "forward" in pos or "attack" in pos:
        return "ATT"
    return "MID"  # default


def api_position_to_game_role(position: str) -> str:
    """Map API position to the game's role string format."""
    pos = (position or "").lower()
    if "goal" in pos:
        return "GK"
    elif "centre-back" in pos or "centre back" in pos:
        return "D C"
    elif "left-back" in pos or "left back" in pos:
        return "D/WB L"
    elif "right-back" in pos or "right back" in pos:
        return "D/WB R"
    elif "defence" in pos or "defender" in pos:
        return "D C"
    elif "defensive midfield" in pos:
        return "DM"
    elif "central midfield" in pos:
        return "DM, AM C"
    elif "attacking midfield" in pos:
        return "AM C"
    elif "left midfield" in pos or "left winger" in pos:
        return "AM LC"
    elif "right midfield" in pos or "right winger" in pos:
        return "AM RC, F C"
    elif "midfield" in pos:
        return "AM C"
    elif "centre-forward" in pos or "centre forward" in pos or "striker" in pos:
        return "ST"
    elif "offence" in pos or "forward" in pos or "attack" in pos:
        return "AM RLC, F C"
    return "AM C"


def api_position_to_formation_role(position: str) -> str:
    """Map API position to formation role identifier (used in formation XML)."""
    pos = (position or "").lower()
    if "goal" in pos:
        return "GK"
    elif "centre-back" in pos or "centre back" in pos:
        return "CB"
    elif "left-back" in pos or "left back" in pos:
        return "LB"
    elif "right-back" in pos or "right back" in pos:
        return "RB"
    elif "defence" in pos or "defender" in pos:
        return "CB"
    elif "defensive midfield" in pos:
        return "DM"
    elif "central midfield" in pos:
        return "CM"
    elif "attacking midfield" in pos:
        return "AM"
    elif "left midfield" in pos or "left winger" in pos:
        return "LM"
    elif "right midfield" in pos or "right winger" in pos:
        return "RM"
    elif "midfield" in pos:
        return "CM"
    elif "centre-forward" in pos or "centre forward" in pos or "striker" in pos:
        return "CF"
    elif "offence" in pos or "forward" in pos or "attack" in pos:
        return "CF"
    return "CM"


def calculate_age(date_of_birth: str) -> int:
    """Calculate age from date of birth string (YYYY-MM-DD)."""
    if not date_of_birth:
        return random.randint(20, 30)
    try:
        from datetime import date
        parts = date_of_birth.split("-")
        birth = date(int(parts[0]), int(parts[1]), int(parts[2]))
        today = date.today()
        age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        return max(16, min(40, age))
    except Exception:
        return random.randint(20, 30)


def generate_profile_xml(position_category: str) -> str:
    """Generate profile_xml for a player based on their position category."""
    base_profile = POSITION_PROFILES.get(position_category, POSITION_PROFILES["MID"])
    xml_parts = []
    for stat in STAT_NAMES:
        base_val = base_profile[stat]
        # Add some random variation (±0.1)
        val = base_val + random.uniform(-0.1, 0.1)
        val = max(0.05, min(0.95, val))
        xml_parts.append(f"<{stat}>{val:.6f}</{stat}>")
    return "\n".join(xml_parts)


def generate_base_stat(league_tier: str, position: str) -> float:
    """Generate a player's base_stat based on league quality."""
    # Higher tier = better players on average
    tier_ranges = {
        "PL":  (0.55, 0.72), "BL1": (0.54, 0.70),
        "PD":  (0.55, 0.72), "SA":  (0.53, 0.70),
        "FL1": (0.52, 0.68), "DED": (0.50, 0.67),
        "PPL": (0.50, 0.66), "ELC": (0.48, 0.62),
        "BSA": (0.50, 0.67),
    }
    low, high = tier_ranges.get(league_tier, (0.50, 0.65))
    return round(random.uniform(low, high), 6)


def generate_formation_xml(formation_name: str = "4-4-2") -> str:
    """Generate formation XML from a formation template."""
    formation = FORMATIONS.get(formation_name, FORMATIONS["4-4-2"])
    parts = []
    for i, (x, y, role) in enumerate(formation, 1):
        parts.append(
            f"<p{i}><position>{x:.1f}, {y:5.2f}</position>"
            f"<role>{role}</role></p{i}>"
        )
    return "".join(parts)


def generate_tactics_xml() -> str:
    """Generate randomized but reasonable tactics XML."""
    return DEFAULT_TACTICS.format(
        dcm=random.uniform(0.35, 0.60),
        do=random.uniform(0.50, 0.80),
        pddf=random.uniform(0.45, 0.70),
        pdms=random.uniform(0.50, 0.80),
        pdmf=random.uniform(0.45, 0.70),
        pdss=random.uniform(0.30, 0.55),
        pdwf=random.uniform(0.40, 0.65),
        podf=random.uniform(0.45, 0.70),
        poms=random.uniform(0.45, 0.65),
        pomf=random.uniform(0.60, 0.90),
        poss=random.uniform(0.25, 0.50),
        powf=random.uniform(0.60, 0.90),
    )


def generate_player_appearance(nationality: str = None):
    """Generate random player appearance attributes."""
    skincolor = random.randint(1, 4)
    hairstyle = random.choice(HAIRSTYLES)
    haircolor = random.choice(HAIR_COLORS)
    height = round(random.uniform(1.68, 1.95), 2)
    weight = round(random.uniform(65.0, 90.0), 1)
    return skincolor, hairstyle, haircolor, height, weight


def split_name(full_name: str):
    """Split a full name into first and last name."""
    if not full_name:
        return "Unknown", "Player"
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


# Image Generation
def find_fallback_logo(data_dir: str) -> str:
    """Find a robust fallback logo from existing assets."""
    try:
        candidates = glob.glob(os.path.join(data_dir, "databases", "default", "images_teams", "*", "*_logo.png"))
        if candidates:
            return candidates[0]
    except:
        pass
    return os.path.join(data_dir, "databases", "default", "template_kit_base.png")


def download_image(url: str, output_path: str):
    """Download an image from a URL, converting SVG to PNG if needed."""
    if not url:
        return False
    
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            if url.lower().endswith(".svg"):
                 return False
            else:
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                return True
    except Exception as e:
        print(f"    Download failed: {e}")
    return False


def generate_kit_image(template_path: str, output_path: str,
                       primary_color: tuple, secondary_color: tuple):
    if HAS_PILLOW:
        try:
            template = Image.open(template_path).convert("RGBA")
            result = Image.new("RGBA", template.size, (0, 0, 0, 255))
            pixels = template.load()
            result_pixels = result.load()

            for y in range(template.height):
                for x in range(template.width):
                    r, g, b, a = pixels[x, y]

                    # Black areas stay black (background/separators)
                    if r < 30 and g < 30 and b < 30:
                        result_pixels[x, y] = (0, 0, 0, a)
                    # Light/white areas = primary color (shirt body)
                    elif r > 180 and g > 180 and b > 180:
                        lum = (r + g + b) / (3 * 255)
                        pr = int(primary_color[0] * lum)
                        pg = int(primary_color[1] * lum)
                        pb = int(primary_color[2] * lum)
                        result_pixels[x, y] = (pr, pg, pb, a)
                    # Mid-gray areas = secondary color (accents
                    elif 80 < r < 180 and 80 < g < 180 and 80 < b < 180:
                        lum = (r + g + b) / (3 * 255)
                        sr = int(secondary_color[0] * lum)
                        sg = int(secondary_color[1] * lum)
                        sb = int(secondary_color[2] * lum)
                        result_pixels[x, y] = (sr, sg, sb, a)
                    # Green text areas - use primary color
                    elif g > r and g > b:
                        result_pixels[x, y] = (0, 0, 0, a)
                    else:
                        lum = (r + g + b) / (3 * 255)
                        pr = int(primary_color[0] * lum)
                        pg = int(primary_color[1] * lum)
                        pb = int(primary_color[2] * lum)
                        result_pixels[x, y] = (pr, pg, pb, a)

            result.save(output_path, "PNG")
            return
        except Exception as e:
            print(f"  WARNING: Failed to generate kit image: {e}")

    try:
        shutil.copy2(template_path, output_path)
    except Exception as e:
        print(f"  ERROR: Failed to copy fallback kit: {e}")


def generate_logo_image(output_path: str, primary_color: tuple,
                        secondary_color:tuple, shortname: str, download_url: str = None, fallback_source: str = None):
    if download_url and download_image(download_url, output_path):
        return

    if HAS_PILLOW:
        try:
            img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            draw.ellipse([16, 16, 240, 240], fill=primary_color, outline=secondary_color, width=4)
            
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72)
            except:
                font = None

            text = shortname[:3] if shortname else "???"
            if font:
                try:
                    bbox = draw.textbbox((0, 0), text, font=font)
                    text_width = bbox[2] - bbox[0]
                    text_height = bbox[3] - bbox[1]
                    x = (256 - text_width) // 2
                    y = (256 - text_height) // 2 - 10
                    draw.text((x, y), text, fill=secondary_color, font=font)
                except:
                    pass
            
            img.save(output_path, "PNG")
            return
        except Exception as e:
            print(f"  WARNING: Failed to generate logo: {e}")

    if fallback_source and os.path.exists(fallback_source):
        try:
            shutil.copy2(fallback_source, output_path)
            return
        except:
            pass

# API Client

class FootballDataAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"X-Auth-Token": api_key})
        self.last_request_time = 0

    def _rate_limit(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            wait = RATE_LIMIT_DELAY - elapsed
            print(f"  (rate limit: waiting {wait:.1f}s...)")
            time.sleep(wait)
        self.last_request_time = time.time()

    def get(self, endpoint: str) -> dict:
        self._rate_limit()
        url = f"{API_BASE}{endpoint}"
        print(f"  GET {url}")
        resp = self.session.get(url, timeout=30)
        if resp.status_code == 429:
            print("  Rate limited! Waiting 60s...")
            time.sleep(60)
            return self.get(endpoint)
        resp.raise_for_status()
        return resp.json()

    def get_competition_teams(self, code: str) -> list:
        data = self.get(f"/competitions/{code}/teams")
        return data.get("teams", [])


# Database Builder

class DatabaseBuilder:
    def __init__(self, db_path: str, data_dir: str):
        self.db_path = db_path
        self.data_dir = data_dir
        self.conn = None
        self.region_id = 0
        self.country_id = 0
        self.league_id = 0
        self.team_id = 0
        self.player_id = 0
        self.regions = {} 
        self.countries = {}

    def connect(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")

    def create_tables(self):
        self.conn.executescript("""
            DROP TABLE IF EXISTS players;
            DROP TABLE IF EXISTS teams;
            DROP TABLE IF EXISTS leagues;
            DROP TABLE IF EXISTS countries;
            DROP TABLE IF EXISTS regions;
            DROP TABLE IF EXISTS tournaments;
            DROP TABLE IF EXISTS tournamentdata;

            CREATE TABLE regions(id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(64));
            CREATE TABLE countries(id INTEGER PRIMARY KEY AUTOINCREMENT, region_id INTEGER, name VARCHAR(64));
            CREATE TABLE leagues(id INTEGER PRIMARY KEY AUTOINCREMENT, country_id INTEGER, name VARCHAR(64), logo_url VARCHAR(512));
            CREATE TABLE teams(id INTEGER PRIMARY KEY AUTOINCREMENT, league_id INTEGER, name VARCHAR(64), logo_url VARCHAR(512), kit_url VARCHAR(512), formation_xml TEXT, formation_factory_xml TEXT, tactics_xml TEXT, tactics_factory_xml TEXT, shortname VARCHAR(3), color1 VARCHAR(16), color2 VARCHAR(16));
            CREATE TABLE players(id INTEGER PRIMARY KEY AUTOINCREMENT, team_id INTEGER, nationalteam_id INTEGER, firstname VARCHAR(64), lastname VARCHAR(64), role VARCHAR(32), age INTEGER, base_stat FLOAT, profile_xml TEXT, skincolor INTEGER, hairstyle VARCHAR(64), haircolor VARCHAR(64), height FLOAT, weight FLOAT, formationorder INTEGER, nationalteamformationorder INTEGER);
            CREATE TABLE tournaments(id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(64), location_type INTEGER, location_id INTEGER, has_knockoutphase INTEGER, has_groupphase INTEGER, prizemoney INTEGER);
            CREATE TABLE tournamentdata(id INTEGER PRIMARY KEY AUTOINCREMENT, tournament_id INTEGER, year INTEGER, xmldata TEXT);
        """)
        self.conn.commit()

    def get_or_create_region(self, name: str) -> int:
        if name in self.regions: return self.regions[name]
        self.region_id += 1
        self.conn.execute("INSERT INTO regions (id, name) VALUES (?, ?)", (self.region_id, name))
        self.regions[name] = self.region_id
        return self.region_id

    def get_or_create_country(self, name: str, region_name: str) -> int:
        if name in self.countries: return self.countries[name]
        region_id = self.get_or_create_region(region_name)
        self.country_id += 1
        self.conn.execute("INSERT INTO countries (id, region_id, name) VALUES (?, ?, ?)", (self.country_id, region_id, name))
        self.countries[name] = self.country_id
        return self.country_id

    def add_league(self, name: str, country_name: str, region_name: str, logo_url: str) -> int:
        country_id = self.get_or_create_country(country_name, region_name)
        self.league_id += 1
        self.conn.execute("INSERT INTO leagues (id, country_id, name, logo_url) VALUES (?, ?, ?, ?)", (self.league_id, country_id, name, logo_url))
        return self.league_id

    def add_team(self, league_id: int, name: str, shortname: str, logo_url: str, kit_url: str, formation_xml: str, tactics_xml: str, color1: str, color2: str) -> int:
        self.team_id += 1
        self.conn.execute("INSERT INTO teams (id, league_id, name, logo_url, kit_url, formation_xml, formation_factory_xml, tactics_xml, tactics_factory_xml, shortname, color1, color2) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (self.team_id, league_id, name, logo_url, kit_url, formation_xml, formation_xml, tactics_xml, tactics_xml, shortname, color1, color2))
        return self.team_id

    def add_player(self, team_id: int, firstname: str, lastname: str, role: str, age: int, base_stat: float, profile_xml: str, skincolor: int, hairstyle: str, haircolor: str, height: float, weight: float, formationorder: int) -> int:
        self.player_id += 1
        self.conn.execute("INSERT INTO players (id, team_id, nationalteam_id, firstname, lastname, role, age, base_stat, profile_xml, skincolor, hairstyle, haircolor, height, weight, formationorder, nationalteamformationorder) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (self.player_id, team_id, 0, firstname, lastname, role, age, base_stat, profile_xml, skincolor, hairstyle, haircolor, height, weight, formationorder, 0))
        return self.player_id

    def commit(self):
        self.conn.commit()
    def close(self):
        if self.conn: self.conn.close()


def pick_formation_for_squad(squad: list) -> str:
    pos_counts = {"GK": 0, "DEF": 0, "MID": 0, "ATT": 0}
    for player in squad:
        cat = api_position_to_game_category(player.get("position", ""))
        pos_counts[cat] = pos_counts.get(cat, 0) + 1
    if pos_counts["ATT"] >= 5: return "4-3-3"
    elif pos_counts["MID"] >= 7: return "4-2-3-1"
    elif pos_counts["DEF"] >= 6: return "3-5-2"
    else: return "4-4-2"

def select_starting_11_and_subs(squad: list, formation_name: str) -> list:
    formation = FORMATIONS[formation_name]
    needed_roles = [role for _, _, role in formation]
    
    goalkeepers = [p for p in squad if api_position_to_formation_role(p.get("position", "")) == "GK"]
    defenders = [p for p in squad if api_position_to_game_category(p.get("position", "")) == "DEF"]
    midfielders = [p for p in squad if api_position_to_game_category(p.get("position", "")) == "MID"]
    attackers = [p for p in squad if api_position_to_game_category(p.get("position", "")) == "ATT"]

    selected = []
    used = set()

    for slot_idx, role in enumerate(needed_roles):
        candidate = None
        if role == "GK" and goalkeepers:
            for gk in goalkeepers:
                if id(gk) not in used: candidate = gk; break
        elif role in ("CB", "LB", "RB") and defenders:
            for d in defenders:
                if id(d) not in used: candidate = d; break
        elif role in ("DM", "CM", "AM", "LM", "RM") and midfielders:
            for m in midfielders:
                if id(m) not in used: candidate = m; break
        elif role in ("CF",) and attackers:
            for a in attackers:
                if id(a) not in used: candidate = a; break
        if candidate is None:
            for p in squad:
                if id(p) not in used: candidate = p; break
        if candidate:
            used.add(id(candidate))
            selected.append((candidate, slot_idx))

    sub_idx = 11
    for p in squad:
        if len(selected) >= 18: break
        if id(p) not in used:
            used.add(id(p))
            selected.append((p, sub_idx))
            sub_idx += 1

    while len(selected) < 18:
        filler = {"name": f"Player {len(selected)+1}", "position": random.choice(["Defence", "Midfield", "Offence"]), "dateOfBirth": None, "nationality": None}
        selected.append((filler, sub_idx))
        sub_idx += 1
    return selected

def import_league(api: FootballDataAPI, db: DatabaseBuilder, league_code: str, data_dir: str, template_path: str, fallback_logo: str):
    info = LEAGUE_INFO.get(league_code)
    if not info: return

    print(f"\nImporting: {info['name']} ({league_code})")

    try: teams = api.get_competition_teams(league_code)
    except: return

    if not teams: return

    league_dirname = sanitize_dirname(info["name"])
    league_img_dir = os.path.join(data_dir, "databases", "default", "images_teams", league_dirname)
    os.makedirs(league_img_dir, exist_ok=True)
    comp_img_dir = os.path.join(data_dir, "databases", "default", "images_competitions")
    os.makedirs(comp_img_dir, exist_ok=True)

    region = COUNTRY_REGIONS.get(info["country"], "Europe")
    logo_url = f"images_competitions/{league_dirname}.png"
    comp_logo_path = os.path.join(comp_img_dir, f"{league_dirname}.png")
    
    if not os.path.exists(comp_logo_path):
        generate_logo_image(comp_logo_path, (50, 100, 200), (255, 255, 255), league_code, None, fallback_logo)

    league_id = db.add_league(info["name"], info["country"], region, logo_url)

    for team_data in teams:
        team_name = team_data.get("shortName") or team_data.get("name", "Unknown FC")
        tla = team_data.get("tla", "")[:3] or team_name[:3].upper()
        club_colors = team_data.get("clubColors", "")
        crest_url = team_data.get("crest")
        squad = team_data.get("squad", [])

        # print(f"  Team: {team_name}")

        c1, c2 = parse_club_colors(club_colors)
        color1_str = color_to_db(c1)
        color2_str = color_to_db(c2)

        team_dirname = sanitize_dirname(team_name)
        team_img_base = os.path.join(league_img_dir, team_dirname)

        formation_name = pick_formation_for_squad(squad) if squad else "4-4-2"
        formation_xml = generate_formation_xml(formation_name)
        tactics_xml = generate_tactics_xml()

        logo_rel = f"images_teams/{league_dirname}/{team_dirname}_logo.png"
        kit_rel = f"images_teams/{league_dirname}/{team_dirname}"

        kit_01_path = f"{team_img_base}_kit_01.png"
        kit_02_path = f"{team_img_base}_kit_02.png"
        logo_path = f"{team_img_base}_logo.png"

        generate_kit_image(template_path, kit_01_path, c1, c2)
        generate_kit_image(template_path, kit_02_path, c2, c1)
        generate_logo_image(logo_path, c1, c2, tla, crest_url, fallback_logo)

        team_id = db.add_team(league_id, team_name, tla, logo_rel, kit_rel, formation_xml, tactics_xml, color1_str, color2_str)

        if squad: roster = select_starting_11_and_subs(squad, formation_name)
        else:
            roster = []
            positions = ["Goalkeeper"]*2 + ["Defence"]*5 + ["Midfield"]*6 + ["Offence"]*5
            for i, pos in enumerate(positions):
                filler = {"name": f"{team_name} Player {i+1}", "position": pos, "dateOfBirth": None, "nationality": None}
                roster.append((filler, i))

        for player_data, formation_order in roster:
            name = player_data.get("name", "Unknown Player")
            firstname, lastname = split_name(name)
            position = player_data.get("position", "Midfield")
            age = calculate_age(player_data.get("dateOfBirth"))
            pos_category = api_position_to_game_category(position)
            role = api_position_to_game_role(position)
            base_stat = generate_base_stat(league_code, position)
            profile_xml = generate_profile_xml(pos_category)
            skincolor, hairstyle, haircolor, height, weight = generate_player_appearance()
            db.add_player(team_id, firstname, lastname, role, age, base_stat, profile_xml, skincolor, hairstyle, haircolor, height, weight, formation_order)

    db.commit()
    print(f"  ✓ {info['name']}: {len(teams)} teams imported")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--leagues", default="PL,BL1,PD,SA")
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--out-dir", default=None, help="Output directory for database and images")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)
    
    if args.out_dir:
        data_dir = os.path.abspath(args.out_dir)
    else:
        data_dir = os.path.join(project_dir, "data")
        
    db_path = args.db_path or os.path.join(data_dir, "databases", "default", "database.sqlite")
    template_path = os.path.join(data_dir, "databases", "default", "template_kit_base.png")
    
    # Find a fallback logo
    fallback_logo = find_fallback_logo(data_dir)
    
    leagues = [l.strip().upper() for l in args.leagues.split(",")]

    if os.path.exists(db_path) and not args.no_backup:
        shutil.copy2(db_path, db_path + ".backup")

    api = FootballDataAPI(args.api_key)
    db = DatabaseBuilder(db_path, data_dir)
    db.connect()
    db.create_tables()

    for league_code in leagues:
        import_league(api, db, league_code, data_dir, template_path, fallback_logo)

    db.commit()
    db.close()
    print("\nIMPORT COMPLETE!")

if __name__ == "__main__":
    main()
