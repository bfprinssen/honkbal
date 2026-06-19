import array
import math
import random
import sys
from dataclasses import dataclass
from typing import Optional

import pygame


WIDTH = 1280
HEIGHT = 720
FPS = 60

STRIKE_ZONE = pygame.Rect(490, 252, 300, 270)
ZONE_CENTER = pygame.Vector2(STRIKE_ZONE.center)
PCI_LIMIT = STRIKE_ZONE.inflate(190, 170)

PLATE_SCREEN = pygame.Vector2(WIDTH / 2, 662)
FIELD_SCALE = 1.34
WALL_DISTANCE = 392.0
WALL_HEIGHT = 9.0
GRAVITY = 32.0
MPH_TO_FPS = 1.46667


def clamp(value, low, high):
    return max(low, min(high, value))


def lerp(a, b, t):
    return a + (b - a) * t


def mix_color(a, b, t):
    return (
        int(lerp(a[0], b[0], t)),
        int(lerp(a[1], b[1], t)),
        int(lerp(a[2], b[2], t)),
    )


def smoothstep(t):
    t = clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def norm_to_zone(x_norm, y_norm):
    x = ZONE_CENTER.x + x_norm * STRIKE_ZONE.width * 0.5
    y = ZONE_CENTER.y - y_norm * STRIKE_ZONE.height * 0.5
    return pygame.Vector2(x, y)


def zone_to_norm(pos):
    return pygame.Vector2(
        (pos.x - ZONE_CENTER.x) / (STRIKE_ZONE.width * 0.5),
        (ZONE_CENTER.y - pos.y) / (STRIKE_ZONE.height * 0.5),
    )


def world_to_screen(x, y, z=0.0):
    return pygame.Vector2(
        PLATE_SCREEN.x + x * FIELD_SCALE,
        PLATE_SCREEN.y - y * FIELD_SCALE - z * 0.86,
    )


def base_world(index):
    positions = {
        -1: pygame.Vector2(0, 0),
        0: pygame.Vector2(90, 90),
        1: pygame.Vector2(0, 180),
        2: pygame.Vector2(-90, 90),
        3: pygame.Vector2(0, 0),
    }
    return positions[index].copy()


def fair_at(x, y):
    if y < 18:
        return True
    return abs(x) <= y * 1.02 + 8


def distance_ft(x, y):
    return math.hypot(x, y)


def draw_vertical_gradient(surface, top_color, bottom_color, rect=None):
    rect = pygame.Rect(rect) if rect else surface.get_rect()
    height = max(1, rect.height - 1)
    for y in range(rect.top, rect.bottom):
        t = (y - rect.top) / height
        pygame.draw.line(surface, mix_color(top_color, bottom_color, t), (rect.left, y), (rect.right, y))


def draw_glass_panel(surface, rect, fill=(12, 18, 28, 190), border=(255, 255, 255, 42), radius=8):
    rect = pygame.Rect(rect)
    panel = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(panel, fill, panel.get_rect(), border_radius=radius)
    pygame.draw.rect(panel, (255, 255, 255, 22), (1, 1, rect.width - 2, max(8, rect.height // 3)), border_radius=radius)
    pygame.draw.rect(panel, border, panel.get_rect().inflate(-1, -1), 1, border_radius=radius)
    surface.blit(panel, rect.topleft)


def draw_soft_circle(surface, pos, radius, color, layers=7):
    radius = int(radius)
    size = radius * 2 + 6
    glow = pygame.Surface((size, size), pygame.SRCALPHA)
    center = (size // 2, size // 2)
    r, g, b, alpha = color
    for i in range(layers, 0, -1):
        frac = i / layers
        layer_radius = max(1, int(radius * frac))
        layer_alpha = int(alpha * (1.0 - frac + 1.0 / layers) ** 1.8)
        pygame.draw.circle(glow, (r, g, b, layer_alpha), center, layer_radius)
    surface.blit(glow, (pos[0] - size // 2, pos[1] - size // 2))


@dataclass(frozen=True)
class PitchSpec:
    name: str
    min_speed: int
    max_speed: int
    horizontal_break: float
    vertical_break: float
    difficulty: float
    color: tuple[int, int, int]


PITCH_SPECS = [
    PitchSpec("Fastball", 94, 101, 6.0, -2.0, 0.96, (245, 247, 255)),
    PitchSpec("Slider", 83, 90, -42.0, 8.0, 0.82, (255, 229, 181)),
    PitchSpec("Curveball", 76, 83, 12.0, 54.0, 0.78, (208, 228, 255)),
    PitchSpec("Changeup", 78, 86, 9.0, 28.0, 0.86, (231, 255, 214)),
    PitchSpec("Sinker", 90, 96, 14.0, 36.0, 0.88, (255, 232, 220)),
]


SWING_TYPES = {
    "normal": {
        "label": "Normal",
        "pci": 76.0,
        "power": 1.0,
        "timing": 1.0,
        "contact": 1.0,
    },
    "power": {
        "label": "Power",
        "pci": 58.0,
        "power": 1.14,
        "timing": 0.84,
        "contact": 0.86,
    },
    "contact": {
        "label": "Contact",
        "pci": 94.0,
        "power": 0.86,
        "timing": 1.17,
        "contact": 1.2,
    },
}


TIMING_COLORS = {
    "Perfect Timing": (255, 236, 120),
    "Good Timing": (104, 230, 154),
    "Early": (255, 184, 96),
    "Very Early": (255, 111, 90),
    "Late": (111, 191, 255),
    "Very Late": (96, 125, 255),
    "No Swing": (215, 219, 229),
    "Miss": (255, 105, 105),
}


class SoundManager:
    def __init__(self):
        self.enabled = False
        self.sounds = {}
        self.crowd_channel = None
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=1, buffer=512)
            self.enabled = True
            self.crowd_channel = pygame.mixer.Channel(7)
            self._build_sounds()
            self.sounds["crowd"].set_volume(0.18)
            self.crowd_channel.play(self.sounds["crowd"], loops=-1)
        except pygame.error:
            self.enabled = False

    def play(self, name, volume=1.0):
        if not self.enabled or name not in self.sounds:
            return
        sound = self.sounds[name]
        sound.set_volume(volume)
        sound.play()

    def _tone(self, seconds, freq_a, freq_b=None, volume=0.45, noise=0.0, pulse=0.0):
        sample_rate = 44100
        total = max(1, int(seconds * sample_rate))
        freq_b = freq_a if freq_b is None else freq_b
        data = array.array("h")
        phase = 0.0
        for i in range(total):
            t = i / total
            freq = lerp(freq_a, freq_b, t)
            phase += (2.0 * math.pi * freq) / sample_rate
            env = (1.0 - t) ** 1.85
            if pulse:
                env *= 0.65 + 0.35 * math.sin(t * math.pi * pulse) ** 2
            value = math.sin(phase) * env
            if noise:
                value += random.uniform(-1.0, 1.0) * noise * env
            data.append(int(clamp(value * volume, -1.0, 1.0) * 32767))
        return pygame.mixer.Sound(buffer=data.tobytes())

    def _crowd(self):
        sample_rate = 44100
        seconds = 3.2
        total = int(seconds * sample_rate)
        data = array.array("h")
        phase_a = 0.0
        phase_b = 0.0
        for i in range(total):
            t = i / sample_rate
            phase_a += 2.0 * math.pi * 115.0 / sample_rate
            phase_b += 2.0 * math.pi * 167.0 / sample_rate
            wave = math.sin(phase_a) * 0.11 + math.sin(phase_b) * 0.08
            wave += random.uniform(-1.0, 1.0) * 0.22
            swell = 0.55 + 0.45 * math.sin(t * 2.0 * math.pi / seconds) ** 2
            data.append(int(clamp(wave * swell * 0.55, -1.0, 1.0) * 32767))
        return pygame.mixer.Sound(buffer=data.tobytes())

    def _build_sounds(self):
        self.sounds["pitch"] = self._tone(0.34, 960, 290, volume=0.35, noise=0.45)
        self.sounds["swing"] = self._tone(0.22, 180, 58, volume=0.36, noise=0.8)
        self.sounds["contact"] = self._tone(0.20, 126, 62, volume=0.68, noise=0.55, pulse=12.0)
        self.sounds["home_run"] = self._tone(1.15, 392, 784, volume=0.47, noise=0.18, pulse=9.0)
        self.sounds["crowd"] = self._crowd()


class Pitch:
    def __init__(self, now):
        self.spec = random.choices(
            PITCH_SPECS,
            weights=[31, 20, 15, 17, 17],
            k=1,
        )[0]
        self.speed = random.randint(self.spec.min_speed, self.spec.max_speed)
        self.start_time = now
        real_reaction = 60.5 / (self.speed * MPH_TO_FPS)
        self.duration = clamp(real_reaction * 1.9 + 0.14, 0.72, 1.16)
        self.release_point = pygame.Vector2(WIDTH * 0.5 + random.uniform(-9, 9), 262 + random.uniform(-5, 4))
        self.target_norm = self._choose_target()
        self.target_point = norm_to_zone(self.target_norm.x, self.target_norm.y)
        self.tail = []

    @property
    def contact_time(self):
        return self.start_time + self.duration

    @property
    def in_zone(self):
        return abs(self.target_norm.x) <= 1.0 and abs(self.target_norm.y) <= 1.0

    def _choose_target(self):
        in_zone = random.random() < 0.64
        if in_zone:
            x = random.triangular(-0.95, 0.95, 0.0)
            y = random.triangular(-0.95, 0.95, 0.0)
            if random.random() < 0.28:
                x = random.choice([-1, 1]) * random.uniform(0.74, 1.02)
            if random.random() < 0.26:
                y = random.choice([-1, 1]) * random.uniform(0.72, 1.02)
        else:
            side = random.choice(["left", "right", "high", "low", "corner"])
            if side == "left":
                x = random.uniform(-1.48, -1.08)
                y = random.uniform(-1.06, 1.08)
            elif side == "right":
                x = random.uniform(1.08, 1.48)
                y = random.uniform(-1.06, 1.08)
            elif side == "high":
                x = random.uniform(-1.05, 1.05)
                y = random.uniform(1.08, 1.42)
            elif side == "low":
                x = random.uniform(-1.05, 1.05)
                y = random.uniform(-1.42, -1.08)
            else:
                x = random.choice([-1, 1]) * random.uniform(1.02, 1.36)
                y = random.choice([-1, 1]) * random.uniform(1.02, 1.34)
        return pygame.Vector2(x, y)

    def progress(self, now):
        return (now - self.start_time) / self.duration

    def screen_position(self, now):
        t = clamp(self.progress(now), 0.0, 1.12)
        t01 = clamp(t, 0.0, 1.0)
        visual_t = smoothstep(t01)
        break_vector = pygame.Vector2(self.spec.horizontal_break, self.spec.vertical_break)
        aim_point = self.target_point - break_vector
        base = self.release_point.lerp(aim_point, visual_t)
        break_t = t01 ** 1.55
        pos = base + break_vector * break_t
        radius = lerp(4.0, 17.0, clamp(t, 0.0, 1.0) ** 1.45)
        return pos, radius


class Fielder:
    def __init__(self, name, x, y, speed, color):
        self.name = name
        self.home = pygame.Vector2(x, y)
        self.pos = pygame.Vector2(x, y)
        self.speed = speed
        self.color = color
        self.facing = pygame.Vector2(0, 1)
        self.running_speed = 0.0
        self.action = ""
        self.action_until = 0.0

    def reset(self):
        self.pos.update(self.home)
        self.running_speed = 0.0
        self.action = ""
        self.action_until = 0.0

    def move_toward(self, target, dt):
        delta = target - self.pos
        dist = delta.length()
        if dist <= 0.01:
            self.running_speed = 0.0
            return
        step = min(dist, self.speed * dt)
        direction = delta.normalize()
        self.facing = direction
        self.running_speed = step / max(dt, 0.001)
        self.pos += direction * step


class HitBall:
    def __init__(self, exit_velo, launch_angle, spray_angle, spin_bias=0.0):
        self.pos = pygame.Vector3(0.0, 0.0, 3.1)
        speed = exit_velo * MPH_TO_FPS
        launch = math.radians(launch_angle)
        spray = math.radians(spray_angle)
        horizontal = math.cos(launch) * speed
        self.vel = pygame.Vector3(
            math.sin(spray) * horizontal,
            math.cos(spray) * horizontal,
            math.sin(launch) * speed,
        )
        self.spin_bias = spin_bias
        self.launch_angle = launch_angle
        self.spray_angle = spray_angle
        self.exit_velo = exit_velo
        self.elapsed = 0.0
        self.max_distance = 0.0
        self.max_height = self.pos.z
        self.first_landing = None
        self.first_landing_fair = None
        self.bounces = 0
        self.trail = []
        self.home_run = False
        self.foul = False
        self.fielding_roll = random.random()

    @property
    def on_ground(self):
        return self.pos.z <= 0.05 and abs(self.vel.z) < 2.0

    @property
    def horizontal_speed(self):
        return math.hypot(self.vel.x, self.vel.y)

    def update(self, dt):
        self.elapsed += dt
        drag = math.exp(-0.18 * dt)
        self.vel.x *= drag
        self.vel.y *= drag
        self.vel.x += self.spin_bias * dt
        if not self.on_ground:
            self.vel.z -= GRAVITY * dt
        else:
            friction = max(0.0, 1.0 - 0.92 * dt)
            self.vel.x *= friction
            self.vel.y *= friction

        self.pos.x += self.vel.x * dt
        self.pos.y += self.vel.y * dt
        self.pos.z += self.vel.z * dt

        if self.pos.z <= 0.0:
            if self.first_landing is None and self.pos.y > 8.0:
                self.first_landing = pygame.Vector2(self.pos.x, self.pos.y)
                self.first_landing_fair = fair_at(self.pos.x, self.pos.y)
                self.foul = not self.first_landing_fair
            self.pos.z = 0.0
            if self.vel.z < -7.0 and self.bounces < 4:
                self.vel.z = -self.vel.z * (0.22 if self.launch_angle > 12 else 0.11)
                self.vel.x *= 0.62
                self.vel.y *= 0.62
                self.bounces += 1
            else:
                self.vel.z = 0.0

        dist = distance_ft(self.pos.x, self.pos.y)
        self.max_distance = max(self.max_distance, dist)
        self.max_height = max(self.max_height, self.pos.z)
        self.trail.append(pygame.Vector3(self.pos.x, self.pos.y, self.pos.z))
        if len(self.trail) > 42:
            self.trail.pop(0)

        if (
            not self.home_run
            and fair_at(self.pos.x, self.pos.y)
            and self.pos.y >= WALL_DISTANCE
            and abs(self.pos.x) < self.pos.y * 0.95
            and self.pos.z > WALL_HEIGHT
        ):
            self.home_run = True


class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Honkbal: PCI Batting")
        self.fullscreen = True
        self.compact_ui = True
        self.display = self.set_display_mode()
        pygame.mouse.set_visible(False)
        self.screen = pygame.Surface((WIDTH, HEIGHT)).convert()
        self.clock = pygame.time.Clock()
        self.fonts = {
            "small": pygame.font.SysFont("segoeui", 18),
            "body": pygame.font.SysFont("segoeui", 24),
            "bold": pygame.font.SysFont("segoeui", 28, bold=True),
            "big": pygame.font.SysFont("segoeui", 42, bold=True),
            "score": pygame.font.SysFont("consolas", 30, bold=True),
        }
        self.sounds = SoundManager()
        self.rng = random.Random()
        self.running = True

        self.state = "result"
        self.state_until = 0.0
        self.pitch: Optional[Pitch] = None
        self.last_pitch = None
        self.hit_ball: Optional[HitBall] = None
        self.fielders = self._make_fielders()
        self.target_fielder = None
        self.backup_fielder = None
        self.predicted_landing = pygame.Vector2(0, 230)

        self.pci_pos = pygame.Vector2(ZONE_CENTER)
        self.swing_mode = "normal"
        self.swing_flash_until = 0.0
        self.swing_flash_kind = "normal"
        self.has_swung = False
        self.last_swing_time = -10.0

        self.balls = 0
        self.strikes = 0
        self.outs = 0
        self.inning = 1
        self.player_score = 0
        self.cpu_score = 0
        self.bases = [False, False, False]

        self.timing_feedback = "No Swing"
        self.contact_feedback = ""
        self.pitch_feedback = "Klik, Space, X of C om te slaan"
        self.result_text = "Maak de pitcher gek"
        self.secondary_text = "Beweeg de gele PCI met de muis."
        self.last_hit_distance = 0.0
        self.last_hit_type = ""
        self.last_exit_velo = 0.0
        self.last_launch_angle = 0.0
        self.last_spray_angle = 0.0
        self.last_contact_quality = 0.0
        self.last_pci_score = 0.0
        self.last_hit_subtype = ""
        self.hit_card_until = 0.0
        self.message_alpha = 255
        self.cpu_notice = ""
        self.last_runs_scored = 0
        self.total_outs = 0
        self.challenge_best = 0

        self.camera_shake_until = 0.0
        self.camera_shake_power = 0.0
        self.camera_zoom_until = 0.0
        self.camera_zoom_amount = 0.0
        self.slowmo_until = 0.0
        self.result_view = "batter"
        self.runner_anims = []
        self.runner_anim_until = 0.0
        self.last_runner_moves = []
        self.throw_anim = None

        self.countdown_new_pitch(0.65)

    def set_display_mode(self):
        flags = pygame.SCALED
        if self.fullscreen:
            flags |= pygame.FULLSCREEN
        attempts = [flags]
        if self.fullscreen:
            attempts.append(pygame.FULLSCREEN)
        attempts.extend([pygame.SCALED, 0])
        tried = set()
        for mode_flags in attempts:
            if mode_flags in tried:
                continue
            tried.add(mode_flags)
            try:
                display = pygame.display.set_mode((WIDTH, HEIGHT), mode_flags)
                self.fullscreen = bool(mode_flags & pygame.FULLSCREEN)
                return display
            except pygame.error:
                continue
        self.fullscreen = False
        return pygame.display.set_mode((WIDTH, HEIGHT))

    def _make_fielders(self):
        return [
            Fielder("P", 0, 58, 23, (86, 150, 240)),
            Fielder("1B", 88, 94, 26, (86, 150, 240)),
            Fielder("2B", 42, 148, 27, (86, 150, 240)),
            Fielder("SS", -58, 146, 27, (86, 150, 240)),
            Fielder("3B", -88, 94, 26, (86, 150, 240)),
            Fielder("LF", -168, 276, 30, (62, 126, 220)),
            Fielder("CF", 0, 326, 31, (62, 126, 220)),
            Fielder("RF", 168, 276, 30, (62, 126, 220)),
        ]

    def run(self):
        while self.running:
            dt = min(self.clock.tick(FPS) / 1000.0, 0.033)
            now = pygame.time.get_ticks() / 1000.0
            self.handle_events(now)
            self.update(dt * self.time_scale(now), now)
            self.draw(now)
        pygame.quit()

    def time_scale(self, now):
        if self.state == "in_play" and now < self.slowmo_until:
            return 0.38
        return 1.0

    def handle_events(self, now):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_F11:
                    self.fullscreen = not self.fullscreen
                    self.display = self.set_display_mode()
                elif event.key == pygame.K_h:
                    self.compact_ui = not self.compact_ui
                elif event.key == pygame.K_r:
                    self.reset_game()
                elif event.key == pygame.K_SPACE:
                    self.swing("normal", now)
                elif event.key == pygame.K_x:
                    self.swing("power", now)
                elif event.key == pygame.K_c:
                    self.swing("contact", now)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    mods = pygame.key.get_mods()
                    mode = "contact" if mods & pygame.KMOD_SHIFT else "normal"
                    self.swing(mode, now)
                elif event.button == 3:
                    self.swing("power", now)
                elif event.button == 2:
                    self.swing("contact", now)

    def update(self, dt, now):
        mouse = pygame.Vector2(pygame.mouse.get_pos())
        self.pci_pos.x = clamp(mouse.x, PCI_LIMIT.left, PCI_LIMIT.right)
        self.pci_pos.y = clamp(mouse.y, PCI_LIMIT.top, PCI_LIMIT.bottom)

        if self.state == "result" and now >= self.state_until:
            self.start_pitch(now)
        elif self.state == "pitch":
            self.update_pitch(now)
        elif self.state == "in_play":
            self.update_in_play(dt, now)

    def reset_game(self):
        self.balls = 0
        self.strikes = 0
        self.outs = 0
        self.inning = 1
        self.challenge_best = max(self.challenge_best, self.player_score)
        self.player_score = 0
        self.cpu_score = 0
        self.total_outs = 0
        self.bases = [False, False, False]
        self.pitch = None
        self.hit_ball = None
        self.runner_anims = []
        self.throw_anim = None
        self.hit_card_until = 0.0
        self.camera_shake_until = 0.0
        self.camera_zoom_until = 0.0
        self.slowmo_until = 0.0
        self.result_view = "batter"
        self.result_text = "Nieuwe wedstrijd"
        self.secondary_text = "Beweeg de gele PCI en time je swing."
        self.countdown_new_pitch(0.65)

    def countdown_new_pitch(self, delay):
        self.state = "result"
        self.state_until = pygame.time.get_ticks() / 1000.0 + delay

    def start_pitch(self, now):
        self.pitch = Pitch(now)
        self.last_pitch = self.pitch
        self.hit_ball = None
        self.has_swung = False
        self.state = "pitch"
        self.result_view = "batter"
        self.timing_feedback = "No Swing"
        self.contact_feedback = ""
        self.last_hit_type = ""
        self.pitch_feedback = f"{self.pitch.spec.name} - {self.pitch.speed} mph"
        self.result_text = ""
        self.secondary_text = ""
        self.sounds.play("pitch", 0.48)

    def update_pitch(self, now):
        if not self.pitch:
            return
        pos, radius = self.pitch.screen_position(now)
        last_tail_pos = pygame.Vector2(self.pitch.tail[-1][0], self.pitch.tail[-1][1]) if self.pitch.tail else None
        if last_tail_pos is None or (pos - last_tail_pos).length_squared() > 8:
            self.pitch.tail.append((pos.x, pos.y, radius))
            if len(self.pitch.tail) > 20:
                self.pitch.tail.pop(0)
        if self.pitch.progress(now) >= 1.08 and not self.has_swung:
            if self.pitch.in_zone:
                self.called_strike("Called strike")
            else:
                self.add_ball("Ball")

    def swing(self, mode, now):
        if self.state != "pitch" or not self.pitch or self.has_swung:
            return
        self.has_swung = True
        self.last_swing_time = now
        self.swing_flash_until = now + 0.28
        self.swing_flash_kind = mode
        self.swing_mode = mode
        self.sounds.play("swing", 0.55)

        swing_data = SWING_TYPES[mode]
        timing_offset = now - self.pitch.contact_time
        timing_label, timing_score = self.timing_from_offset(timing_offset, swing_data["timing"])
        self.timing_feedback = timing_label

        target = self.pitch.target_point
        pci_radius = swing_data["pci"]
        dist = (self.pci_pos - target).length()
        contact_radius = pci_radius * swing_data["contact"]
        pci_score = clamp(1.0 - dist / contact_radius, 0.0, 1.0)
        edge_bonus = 0.08 if self.pitch.in_zone else -0.1
        contact_quality = clamp(
            (pci_score * 0.66 + timing_score * 0.42 + edge_bonus) * self.pitch.spec.difficulty,
            0.0,
            1.0,
        )

        if timing_score <= 0.0 or contact_quality < 0.27:
            self.contact_feedback = "Miss"
            chase = "Chase strike" if not self.pitch.in_zone else "Swing & miss"
            self.add_strike(chase, swing_miss=True)
            return

        self.create_hit(contact_quality, timing_offset, mode, pci_score)

    def timing_from_offset(self, offset, swing_timing_mod):
        abs_offset = abs(offset)
        perfect = 0.043 * swing_timing_mod
        good = 0.092 * swing_timing_mod
        ok = 0.166 * swing_timing_mod
        ugly = 0.285 * swing_timing_mod
        if abs_offset <= perfect:
            return "Perfect Timing", 1.0
        if abs_offset <= good:
            return "Good Timing", 0.84
        if abs_offset <= ok:
            return ("Early" if offset < 0 else "Late"), 0.55
        if abs_offset <= ugly:
            return ("Very Early" if offset < 0 else "Very Late"), 0.23
        return ("Very Early" if offset < 0 else "Very Late"), 0.0

    def create_hit(self, quality, timing_offset, mode, pci_score):
        assert self.pitch is not None
        target = self.pitch.target_point
        diff = self.pci_pos - target
        swing_data = SWING_TYPES[mode]
        vertical_delta = diff.y / swing_data["pci"]
        horizontal_delta = diff.x / swing_data["pci"]

        timing_bonus = {
            "Perfect Timing": 8.5,
            "Good Timing": 4.0,
            "Early": -3.0,
            "Late": -3.0,
            "Very Early": -11.0,
            "Very Late": -11.0,
        }.get(self.timing_feedback, 0.0)
        exit_velo = (
            52.0
            + quality * 50.0
            + timing_bonus
            + (self.pitch.speed - 82) * 0.22
            + random.uniform(-4.5, 4.5)
        )
        exit_velo *= swing_data["power"]
        exit_velo = clamp(exit_velo, 42.0, 116.5)

        launch = 9.0 + vertical_delta * 23.0 + quality * 9.0 + random.uniform(-7.0, 7.0)
        if mode == "power":
            launch += 3.0
        elif mode == "contact":
            launch -= 2.5
        if quality < 0.42:
            launch += random.uniform(-14.0, 18.0)
        launch = clamp(launch, -10.0, 58.0)

        spray = timing_offset * 154.0 + horizontal_delta * 10.0 + random.uniform(-7.0, 7.0)
        if abs(timing_offset) > 0.13:
            spray += math.copysign(random.uniform(9.0, 18.0), timing_offset)
        if quality < 0.48 and random.random() < 0.45:
            spray += random.choice([-1, 1]) * random.uniform(22.0, 42.0)
        spray = clamp(spray, -68.0, 68.0)

        subtype = "Clean Contact"
        if vertical_delta < -0.52 and quality < 0.72:
            subtype = "Chopper" if random.random() < 0.5 else "Hard Grounder"
            launch = random.uniform(-8.0, 5.0)
            exit_velo = clamp(exit_velo + random.uniform(-6.0, 5.0), 48.0, 106.0)
            spray += random.choice([-1, 1]) * random.uniform(4.0, 14.0)
        elif vertical_delta > 0.62 and quality < 0.7:
            subtype = "Pop Up" if random.random() < 0.55 else "Bloop Fly"
            launch = random.uniform(38.0, 64.0) if subtype == "Pop Up" else random.uniform(22.0, 34.0)
            exit_velo = clamp(exit_velo * random.uniform(0.62, 0.82), 42.0, 78.0)
        elif 0.38 <= quality <= 0.62 and random.random() < 0.24:
            subtype = "Bloop Single"
            launch = random.uniform(18.0, 30.0)
            exit_velo = clamp(exit_velo * random.uniform(0.68, 0.82), 48.0, 76.0)
            spray += random.choice([-1, 1]) * random.uniform(10.0, 22.0)
        elif quality > 0.76 and 6.0 <= launch <= 18.0:
            subtype = "Laser"
            exit_velo = clamp(exit_velo + random.uniform(3.0, 8.0), 80.0, 116.5)
        elif quality > 0.64 and 25.0 <= launch <= 38.0 and exit_velo < 99.0:
            subtype = "Warning Track Fly"

        spray = clamp(spray, -72.0, 72.0)

        spin_bias = random.uniform(-3.0, 3.0) + spray * 0.025
        self.hit_ball = HitBall(exit_velo, launch, spray, spin_bias)
        self.last_exit_velo = exit_velo
        self.last_launch_angle = launch
        self.last_spray_angle = spray
        self.last_contact_quality = quality
        self.last_pci_score = pci_score
        self.last_hit_subtype = subtype
        self.last_hit_type = self.classify_hit_shape(launch)
        self.predicted_landing = self.predict_landing(self.hit_ball)
        self.target_fielder = self.pick_target_fielder(self.predicted_landing)
        self.backup_fielder = self.pick_backup_fielder(self.target_fielder, self.predicted_landing)
        for fielder in self.fielders:
            fielder.reset()
        self.state = "in_play"
        self.result_text = self.last_hit_type
        self.secondary_text = f"{subtype} | Exit velo {exit_velo:.0f} mph | Launch {launch:.0f} deg"
        now = pygame.time.get_ticks() / 1000.0
        self.hit_card_until = now + 5.0
        if self.timing_feedback == "Perfect Timing":
            self.slowmo_until = now + 0.7
            self.pulse_camera(now, shake=8.0, zoom=0.035, duration=0.55)
        else:
            self.pulse_camera(now, shake=4.5 + quality * 5.5, zoom=0.014 + quality * 0.018, duration=0.38)
        self.sounds.play("contact", 0.8)

    def classify_hit_shape(self, launch):
        if launch < 7:
            return "Ground Ball"
        if launch < 18:
            return "Line Drive"
        if launch < 37:
            return "Fly Ball"
        return "High Fly"

    def predict_landing(self, ball):
        sim = HitBall(ball.exit_velo, ball.launch_angle, ball.spray_angle, ball.spin_bias)
        last = pygame.Vector2(0, 120)
        for _ in range(480):
            sim.update(1.0 / 90.0)
            last = pygame.Vector2(sim.pos.x, sim.pos.y)
            if sim.first_landing is not None:
                return sim.first_landing
            if sim.home_run:
                return pygame.Vector2(sim.pos.x, WALL_DISTANCE)
        return last

    def pick_target_fielder(self, landing):
        best = None
        best_dist = 9999
        for fielder in self.fielders:
            d = (fielder.pos - landing).length()
            if d < best_dist:
                best = fielder
                best_dist = d
        return best

    def pick_backup_fielder(self, primary, landing):
        best = None
        best_dist = 9999
        for fielder in self.fielders:
            if fielder is primary:
                continue
            d = (fielder.pos - landing).length()
            if d < best_dist:
                best = fielder
                best_dist = d
        return best

    def mark_fielder_action(self, fielder, action, duration=0.8):
        if not fielder:
            return
        now = pygame.time.get_ticks() / 1000.0
        fielder.action = action
        fielder.action_until = now + duration

    def update_fielder_routes(self, ball, dt):
        primary = self.target_fielder or self.pick_target_fielder(pygame.Vector2(ball.pos.x, ball.pos.y))
        self.target_fielder = primary
        if self.backup_fielder is None:
            self.backup_fielder = self.pick_backup_fielder(primary, self.predicted_landing)

        live_ball = pygame.Vector2(ball.pos.x, ball.pos.y)
        play_target = live_ball if ball.on_ground or ball.first_landing is not None else self.predicted_landing
        cutoff_target = pygame.Vector2(play_target.x * 0.62, max(88.0, play_target.y * 0.58))
        base_cover = {
            "P": pygame.Vector2(52, 82),
            "1B": pygame.Vector2(90, 90),
            "2B": pygame.Vector2(0, 180),
            "SS": pygame.Vector2(-90, 90),
            "3B": pygame.Vector2(-90, 90),
            "LF": pygame.Vector2(-118, 205),
            "CF": pygame.Vector2(0, 235),
            "RF": pygame.Vector2(118, 205),
        }

        for fielder in self.fielders:
            if fielder is primary:
                route = play_target
                speed_mod = 1.0 if ball.first_landing is None else 1.14
                reaction = 0.22 if ball.launch_angle < 10 else 0.36
            elif fielder is self.backup_fielder:
                route = cutoff_target
                speed_mod = 0.76
                reaction = 0.46
            else:
                route = base_cover.get(fielder.name, fielder.home)
                speed_mod = 0.54
                reaction = 0.55
            if ball.elapsed < reaction:
                continue
            old_speed = fielder.speed
            fielder.speed = old_speed * speed_mod
            fielder.move_toward(route, dt)
            fielder.speed = old_speed

    def catch_chance(self, ball, fielder_dist, catch_radius):
        if ball.launch_angle < 6:
            return 0.0
        if ball.launch_angle < 14:
            base = 0.26
        elif ball.launch_angle < 22:
            base = 0.52
        elif ball.max_height > 85:
            base = 0.78
        else:
            base = 0.64
        if ball.exit_velo > 96 and ball.launch_angle < 20:
            base -= 0.2
        if ball.elapsed < 0.68:
            base -= 0.18
        distance_factor = clamp(1.0 - fielder_dist / max(1.0, catch_radius), 0.0, 1.0)
        return clamp(base * (0.45 + distance_factor * 0.55), 0.0, 0.92)

    def update_in_play(self, dt, now):
        if not self.hit_ball:
            return
        ball = self.hit_ball
        ball.update(dt)
        if ball.home_run:
            self.pulse_camera(now, shake=15.0, zoom=0.07, duration=0.9)
            self.resolve_batted_ball("Home Run", out=False, bases=4, special="No doubt!")
            self.sounds.play("home_run", 0.95)
            return

        fair_now = fair_at(ball.pos.x, ball.pos.y) if ball.first_landing_fair is None else ball.first_landing_fair
        self.update_fielder_routes(ball, dt)
        primary = self.target_fielder or min(self.fielders, key=lambda f: (f.pos - pygame.Vector2(ball.pos.x, ball.pos.y)).length_squared())
        fielder_dist = (primary.pos - pygame.Vector2(ball.pos.x, ball.pos.y)).length()

        catch_radius = 6.8
        if ball.launch_angle < 12:
            catch_radius = 4.2
        elif ball.max_height > 75:
            catch_radius = 8.2
        catchable = (
            not ball.on_ground
            and ball.vel.z < 0
            and ball.pos.z <= 9.0
            and fielder_dist <= catch_radius
            and ball.fielding_roll <= self.catch_chance(ball, fielder_dist, catch_radius)
        )
        if catchable:
            self.mark_fielder_action(primary, "catch", 1.1)
            if fair_now:
                self.resolve_batted_ball("Caught", out=True, bases=0, special=f"{primary.name} makes the catch")
            else:
                self.resolve_batted_ball("Foul Out", out=True, bases=0, special=f"{primary.name} catches it foul")
            return

        if ball.first_landing is not None and not ball.first_landing_fair:
            if ball.elapsed > 0.6 and (ball.on_ground or ball.pos.z < 2.0):
                self.resolve_foul(ball.max_distance)
            return

        if ball.on_ground and fielder_dist <= 8.0 and ball.elapsed > 0.35:
            self.resolve_ground_fielded(primary)
            return

        if ball.elapsed > 8.5 or (ball.on_ground and ball.horizontal_speed < 4.0 and ball.elapsed > 1.3):
            self.resolve_open_ball()

    def resolve_ground_fielded(self, fielder):
        assert self.hit_ball is not None
        ball = self.hit_ball
        first_base = pygame.Vector2(90, 90)
        throw_time = (fielder.pos - first_base).length() / 122.0 + 0.33
        runner_time = 4.18
        infield_grounder = ball.max_distance < 145 and ball.launch_angle < 8
        hard_to_side = abs(ball.spray_angle) > 28 and ball.exit_velo > 88
        if infield_grounder and not hard_to_side and ball.elapsed + throw_time < runner_time + random.uniform(-0.12, 0.22):
            self.mark_fielder_action(fielder, "throw", 1.0)
            now = pygame.time.get_ticks() / 1000.0
            self.throw_anim = {
                "start": fielder.pos.copy(),
                "end": first_base,
                "start_time": now,
                "end_time": now + 0.48,
            }
            self.resolve_batted_ball("Ground Out", out=True, bases=0, special=f"{fielder.name} throws to first")
            return
        bases = max(1, self.bases_from_distance(ball.max_distance, ball.launch_angle, ball.spray_angle))
        label = ["", "Single", "Double", "Triple"][bases]
        self.mark_fielder_action(fielder, "field", 0.8)
        self.resolve_batted_ball(label, out=False, bases=bases, special=f"{fielder.name} fields it")

    def resolve_open_ball(self):
        assert self.hit_ball is not None
        ball = self.hit_ball
        bases = self.bases_from_distance(ball.max_distance, ball.launch_angle, ball.spray_angle)
        label = ["", "Single", "Double", "Triple"][bases]
        if bases == 0:
            self.resolve_batted_ball("Out", out=True, bases=0, special="Routine play")
        else:
            self.resolve_batted_ball(label, out=False, bases=bases, special="Ball drops in")

    def bases_from_distance(self, dist, launch, spray):
        if dist > 342 and abs(spray) > 22:
            return 3
        if dist > 262 or (dist > 205 and launch > 15):
            return 2
        if dist > 72:
            return 1
        return 0

    def resolve_foul(self, dist):
        self.last_hit_distance = dist
        if self.strikes < 2:
            self.strikes += 1
            self.result_text = "Foul Ball"
            self.secondary_text = f"Strike {self.strikes} | {dist:.0f} ft"
        else:
            self.result_text = "Foul Ball"
            self.secondary_text = f"Stays {self.balls}-{self.strikes} | {dist:.0f} ft"
        self.hit_ball = None
        self.pitch = None
        self.state = "result"
        self.state_until = pygame.time.get_ticks() / 1000.0 + 1.35

    def resolve_batted_ball(self, label, out, bases, special=""):
        assert self.hit_ball is not None
        self.last_hit_distance = self.hit_ball.max_distance
        runs_scored = 0
        if out:
            self.outs += 1
            self.total_outs += 1
        elif bases > 0:
            runs_scored = self.advance_runners(bases, self.hit_ball)
        self.last_runs_scored = runs_scored
        self.challenge_best = max(self.challenge_best, self.player_score)

        self.result_text = label
        distance_m = self.last_hit_distance * 0.3048
        run_text = ""
        if runs_scored == 1:
            run_text = " | 1 run scores"
        elif runs_scored > 1:
            run_text = f" | {runs_scored} runs score"
        self.secondary_text = f"{special}{run_text} | {self.last_hit_distance:.0f} ft / {distance_m:.0f} m"
        self.balls = 0
        self.strikes = 0
        self.hit_ball = None
        self.pitch = None
        delay = 2.2 if label == "Home Run" else 1.55
        self.check_inning_end()
        self.result_view = "field"
        self.state = "result"
        self.state_until = pygame.time.get_ticks() / 1000.0 + delay

    def add_ball(self, text):
        self.balls += 1
        self.result_text = text
        self.secondary_text = f"Count {self.balls}-{self.strikes}"
        self.pitch = None
        self.result_view = "batter"
        if self.balls >= 4:
            self.walk()
            return
        self.state = "result"
        self.state_until = pygame.time.get_ticks() / 1000.0 + 0.95

    def called_strike(self, text):
        self.add_strike(text, swing_miss=False)

    def add_strike(self, text, swing_miss=False):
        self.strikes += 1
        self.result_text = text
        self.secondary_text = f"Count {self.balls}-{self.strikes}"
        self.pitch = None
        self.result_view = "batter"
        if self.strikes >= 3:
            self.outs += 1
            self.total_outs += 1
            self.result_text = "Strikeout"
            self.secondary_text = "Three strikes"
            self.balls = 0
            self.strikes = 0
            self.check_inning_end()
            self.state = "result"
            self.state_until = pygame.time.get_ticks() / 1000.0 + 1.45
            return
        self.state = "result"
        self.state_until = pygame.time.get_ticks() / 1000.0 + (1.05 if swing_miss else 0.95)

    def walk(self):
        self.result_text = "Walk"
        self.secondary_text = "Four balls"
        self.force_walk_runners()
        self.challenge_best = max(self.challenge_best, self.player_score)
        self.balls = 0
        self.strikes = 0
        self.pitch = None
        self.result_view = "batter"
        self.state = "result"
        self.state_until = pygame.time.get_ticks() / 1000.0 + 1.3

    def force_walk_runners(self):
        if self.bases[0] and self.bases[1] and self.bases[2]:
            self.player_score += 1
        if self.bases[0] and self.bases[1]:
            self.bases[2] = True
        if self.bases[0]:
            self.bases[1] = True
        self.bases[0] = True

    def advance_runners(self, bases, ball=None):
        now = pygame.time.get_ticks() / 1000.0
        self.runner_anims = []
        if bases >= 4:
            runs = 1 + sum(1 for occupied in self.bases if occupied)
            for index, occupied in enumerate(self.bases):
                if occupied:
                    self.runner_anims.append(self.make_runner_anim(index, 3, now, 1.0 + (2 - index) * 0.12))
            self.runner_anims.append(self.make_runner_anim(-1, 3, now, 1.35))
            self.runner_anim_until = now + 1.5
            self.bases = [False, False, False]
            self.player_score += runs
            return runs

        dist = ball.max_distance if ball else 140
        spray = abs(ball.spray_angle) if ball else 0
        deep_gap = dist > 205 or spray > 30
        runs = 0
        new_bases = [False, False, False]
        for index in range(2, -1, -1):
            if not self.bases[index]:
                continue
            advance = bases
            if bases == 1:
                if index == 1 and (dist > 135 or deep_gap or random.random() < 0.58):
                    advance = 2
                elif index == 0 and (dist > 205 or random.random() < 0.2):
                    advance = 2
            elif bases == 2 and index == 0 and (dist > 285 or deep_gap or random.random() < 0.46):
                advance = 3
            new_index = index + advance
            if new_index >= 3:
                runs += 1
                self.runner_anims.append(self.make_runner_anim(index, 3, now, 0.95 + (2 - index) * 0.12))
            else:
                new_bases[new_index] = True
                self.runner_anims.append(self.make_runner_anim(index, new_index, now, 0.85 + advance * 0.16))
        if bases < 4:
            new_bases[bases - 1] = True
            self.runner_anims.append(self.make_runner_anim(-1, bases - 1, now, 0.92 + bases * 0.18))
        self.bases = new_bases
        self.player_score += runs
        self.runner_anim_until = now + 1.55
        return runs

    def make_runner_anim(self, start_base, end_base, now, duration):
        return {
            "start": base_world(start_base),
            "end": base_world(end_base),
            "start_time": now,
            "end_time": now + duration,
            "scored": end_base == 3,
        }

    def check_inning_end(self):
        if self.outs < 3:
            return
        cpu_runs = random.choices([0, 1, 2, 3], weights=[50, 30, 15, 5], k=1)[0]
        self.cpu_score += cpu_runs
        self.cpu_notice = f"CPU scoort {cpu_runs} in de andere helft"
        self.outs = 0
        self.bases = [False, False, False]
        self.balls = 0
        self.strikes = 0
        self.inning += 1
        self.secondary_text = f"{self.secondary_text} | {self.cpu_notice}"

    def draw(self, now):
        self.screen.fill((0, 0, 0))
        show_field_result = self.state == "result" and self.result_view == "field" and now < self.state_until
        if self.state == "in_play" or show_field_result:
            self.draw_field_view(now)
        else:
            self.draw_batter_view(now)
        self.apply_camera_to_display(now)
        old_screen = self.screen
        self.screen = self.display
        self.draw_hud(now)
        self.screen = old_screen
        pygame.display.flip()

    def apply_camera_to_display(self, now):
        self.display.fill((0, 0, 0))
        zoom = 1.0
        if now < self.camera_zoom_until:
            t = clamp((self.camera_zoom_until - now) / 0.9, 0.0, 1.0)
            zoom += self.camera_zoom_amount * smoothstep(t)
        offset = pygame.Vector2(0, 0)
        if now < self.camera_shake_until:
            t = clamp((self.camera_shake_until - now) / 0.45, 0.0, 1.0)
            power = self.camera_shake_power * smoothstep(t)
            offset.update(random.uniform(-power, power), random.uniform(-power, power))
        if abs(zoom - 1.0) > 0.01:
            scaled_size = (int(WIDTH * zoom), int(HEIGHT * zoom))
            scaled = pygame.transform.smoothscale(self.screen, scaled_size)
            rect = scaled.get_rect(center=(WIDTH // 2 + int(offset.x), HEIGHT // 2 + int(offset.y)))
            self.display.blit(scaled, rect)
        else:
            self.display.blit(self.screen, (int(offset.x), int(offset.y)))

    def pulse_camera(self, now, shake=8.0, zoom=0.025, duration=0.45):
        self.camera_shake_power = shake if now >= self.camera_shake_until else max(self.camera_shake_power, shake)
        self.camera_shake_until = max(self.camera_shake_until, now + duration)
        self.camera_zoom_amount = zoom if now >= self.camera_zoom_until else max(self.camera_zoom_amount, zoom)
        self.camera_zoom_until = max(self.camera_zoom_until, now + duration + 0.25)

    def draw_batter_view(self, now):
        self.draw_sky_and_stands()
        self.draw_infield_perspective()
        self.draw_pitcher(now)
        self.draw_strike_zone()
        if self.pitch:
            self.draw_pitch(now)
        self.draw_pci(now)
        if now < self.swing_flash_until:
            self.draw_bat_swing(now)
        self.draw_vignette()
        self.draw_center_feedback(now)

    def draw_vignette(self):
        edge = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        for i in range(18):
            alpha = int(7 + i * 1.7)
            rect = pygame.Rect(i * 9, i * 6, WIDTH - i * 18, HEIGHT - i * 12)
            pygame.draw.rect(edge, (0, 0, 0, alpha), rect, width=12, border_radius=24)
        self.screen.blit(edge, (0, 0))

    def draw_sky_and_stands(self):
        draw_vertical_gradient(self.screen, (8, 18, 35), (36, 86, 60))
        pygame.draw.rect(self.screen, (9, 16, 27), (0, 82, WIDTH, 172))
        pygame.draw.rect(self.screen, (17, 26, 39), (0, 130, WIDTH, 126))
        pygame.draw.rect(self.screen, (47, 82, 66), (0, 238, WIDTH, 54))
        pygame.draw.rect(self.screen, (104, 77, 56), (0, 288, WIDTH, 28))

        scoreboard = pygame.Rect(WIDTH // 2 - 130, 105, 260, 58)
        draw_glass_panel(self.screen, scoreboard, (6, 13, 24, 220), (255, 255, 255, 34), 7)
        title = self.fonts["small"].render("PCI BATTING", True, (255, 226, 112))
        self.screen.blit(title, title.get_rect(center=(scoreboard.centerx, scoreboard.centery - 10)))
        score = self.fonts["score"].render(f"{self.player_score} - {self.cpu_score}", True, (238, 244, 255))
        self.screen.blit(score, score.get_rect(center=(scoreboard.centerx, scoreboard.centery + 14)))

        for row in range(5):
            y = 104 + row * 27
            for x in range(10, WIDTH, 16):
                shade = 44 + ((x * 3 + row * 31) % 58)
                accent = 18 if (x + row) % 7 == 0 else 0
                pygame.draw.circle(self.screen, (shade + accent, shade + 8, shade + 22), (x, y), 3)

        for x in (92, 268, WIDTH - 268, WIDTH - 92):
            pygame.draw.line(self.screen, (69, 82, 97), (x, 78), (x + (18 if x < WIDTH / 2 else -18), 234), 4)
            for i in range(4):
                lx = x + (i - 1.5) * 16
                draw_soft_circle(self.screen, (lx, 72), 18, (255, 244, 190, 72), 5)
                pygame.draw.circle(self.screen, (255, 245, 200), (int(lx), 72), 4)

    def draw_infield_perspective(self):
        grass = [(0, 316), (WIDTH, 316), (WIDTH, HEIGHT), (0, HEIGHT)]
        pygame.draw.polygon(self.screen, (28, 118, 70), grass)
        for i in range(9):
            y = 316 + i * 48
            color = (32, 132, 77) if i % 2 == 0 else (24, 105, 64)
            pygame.draw.polygon(self.screen, color, [(0, y), (WIDTH, y + 28), (WIDTH, y + 72), (0, y + 44)])
        dirt = [(218, 360), (1062, 360), (836, 676), (444, 676)]
        pygame.draw.polygon(self.screen, (147, 101, 65), dirt)
        pygame.draw.polygon(self.screen, (115, 77, 49), dirt, 4)
        mound = pygame.Rect(568, 300, 144, 32)
        pygame.draw.ellipse(self.screen, (165, 117, 76), mound)
        pygame.draw.ellipse(self.screen, (93, 64, 42), mound, 3)
        pygame.draw.rect(self.screen, (229, 223, 204), (620, 307, 40, 4), border_radius=2)
        plate = [(604, 636), (676, 636), (690, 660), (640, 684), (590, 660)]
        pygame.draw.polygon(self.screen, (42, 38, 31), [(598, 641), (682, 641), (698, 664), (640, 692), (582, 664)])
        pygame.draw.polygon(self.screen, (226, 225, 208), plate)
        pygame.draw.polygon(self.screen, (42, 45, 42), plate, 2)
        pygame.draw.line(self.screen, (231, 211, 154), (640, 662), (1038, 356), 3)
        pygame.draw.line(self.screen, (231, 211, 154), (640, 662), (242, 356), 3)
        pygame.draw.arc(self.screen, (98, 68, 45), (410, 436, 460, 188), math.pi, math.tau, 3)
        draw_soft_circle(self.screen, (640, 650), 105, (255, 226, 136, 25), 7)

    def draw_pitcher(self, now):
        base = pygame.Vector2(WIDTH * 0.5, 276)
        progress = 0.0
        if self.pitch:
            progress = clamp(self.pitch.progress(now), 0.0, 1.0)
        leg = math.sin(progress * math.pi * 2.0) * 12
        arm = math.sin(progress * math.pi * 2.4) * 24
        shadow = pygame.Rect(base.x - 36, base.y + 42, 72, 13)
        pygame.draw.ellipse(self.screen, (40, 50, 45), shadow)
        pygame.draw.circle(self.screen, (230, 196, 158), (int(base.x), int(base.y - 52)), 15)
        pygame.draw.rect(self.screen, (235, 238, 245), (base.x - 15, base.y - 38, 30, 45), border_radius=7)
        pygame.draw.rect(self.screen, (58, 90, 162), (base.x - 16, base.y - 5, 32, 25), border_radius=5)
        pygame.draw.line(self.screen, (235, 238, 245), (base.x - 13, base.y - 28), (base.x - 42, base.y - 10 - arm), 7)
        pygame.draw.line(self.screen, (235, 238, 245), (base.x + 13, base.y - 28), (base.x + 38, base.y - 26 + arm * 0.35), 7)
        pygame.draw.line(self.screen, (45, 63, 102), (base.x - 9, base.y + 18), (base.x - 25, base.y + 54 + leg), 8)
        pygame.draw.line(self.screen, (45, 63, 102), (base.x + 9, base.y + 18), (base.x + 24, base.y + 54 - leg * 0.2), 8)
        pygame.draw.circle(self.screen, (35, 42, 54), (int(base.x), int(base.y - 70)), 12)

    def draw_strike_zone(self):
        glow_rect = STRIKE_ZONE.inflate(20, 20)
        glow = pygame.Surface(glow_rect.size, pygame.SRCALPHA)
        pygame.draw.rect(glow, (112, 194, 255, 36), glow.get_rect(), border_radius=8)
        pygame.draw.rect(glow, (255, 255, 255, 20), glow.get_rect().inflate(-8, -8), 1, border_radius=6)
        self.screen.blit(glow, glow_rect.topleft)

        overlay = pygame.Surface((STRIKE_ZONE.width, STRIKE_ZONE.height), pygame.SRCALPHA)
        draw_vertical_gradient(overlay, (23, 35, 50), (10, 16, 27))
        overlay.set_alpha(112)
        pygame.draw.rect(overlay, (222, 237, 255, 172), overlay.get_rect(), 2, border_radius=4)
        for i in (1, 2):
            x = i * STRIKE_ZONE.width / 3
            y = i * STRIKE_ZONE.height / 3
            pygame.draw.line(overlay, (232, 241, 255, 82), (x, 0), (x, STRIKE_ZONE.height), 1)
            pygame.draw.line(overlay, (232, 241, 255, 82), (0, y), (STRIKE_ZONE.width, y), 1)
        self.screen.blit(overlay, STRIKE_ZONE.topleft)

    def draw_pitch(self, now):
        assert self.pitch is not None
        pitch_name = self.pitch.spec.name
        for i, item in enumerate(self.pitch.tail):
            x, y, r = item
            alpha = i / max(1, len(self.pitch.tail) - 1)
            trail_radius = max(1, int(r * (0.36 if pitch_name == "Fastball" else 0.5)))
            color = (
                int(self.pitch.spec.color[0] * alpha),
                int(self.pitch.spec.color[1] * alpha),
                int(self.pitch.spec.color[2] * alpha),
            )
            if pitch_name == "Slider" and i % 2 == 0:
                pygame.draw.line(self.screen, color, (int(x - r * 0.7), int(y)), (int(x + r * 0.7), int(y)), 2)
            elif pitch_name == "Curveball":
                pygame.draw.circle(self.screen, color, (int(x), int(y)), trail_radius, 1)
                if i % 3 == 0:
                    pygame.draw.line(self.screen, color, (int(x), int(y - r * 0.6)), (int(x), int(y + r * 0.6)), 1)
            elif pitch_name == "Changeup":
                pygame.draw.circle(self.screen, color, (int(x), int(y)), trail_radius)
                pygame.draw.circle(self.screen, (80, 255, 170), (int(x), int(y)), max(1, trail_radius + 2), 1)
            elif pitch_name == "Sinker":
                pygame.draw.line(self.screen, color, (int(x - r * 0.3), int(y - r * 0.7)), (int(x + r * 0.3), int(y + r * 0.7)), 2)
            else:
                pygame.draw.circle(self.screen, color, (int(x), int(y)), trail_radius)
        pos, radius = self.pitch.screen_position(now)
        draw_soft_circle(self.screen, (int(pos.x), int(pos.y)), radius * 2.2, (255, 248, 210, 58), 6)
        pygame.draw.circle(self.screen, (250, 250, 246), (int(pos.x), int(pos.y)), int(radius))
        pygame.draw.circle(self.screen, (255, 255, 255), (int(pos.x - radius * 0.25), int(pos.y - radius * 0.25)), max(1, int(radius * 0.18)))
        t = clamp(self.pitch.progress(now), 0.0, 1.0)
        spin_rates = {
            "Fastball": 42.0,
            "Slider": -28.0,
            "Curveball": 18.0,
            "Changeup": 10.0,
            "Sinker": 31.0,
        }
        spin = t * spin_rates.get(pitch_name, 24.0)
        seam_color = (156, 38, 44)
        for angle_offset in (0.0, math.pi):
            angle = spin + angle_offset
            a = pygame.Vector2(math.cos(angle), math.sin(angle)) * radius * 0.58
            b = pygame.Vector2(math.cos(angle + 0.72), math.sin(angle + 0.72)) * radius * 0.58
            pygame.draw.arc(
                self.screen,
                seam_color,
                (pos.x - radius * 0.62 + a.x * 0.1, pos.y - radius * 0.62 + b.y * 0.1, radius * 1.22, radius * 1.22),
                angle - 0.8,
                angle + 0.8,
                max(1, int(radius * 0.12)),
            )
        pygame.draw.arc(
            self.screen,
            (170, 42, 48),
            (pos.x - radius * 0.72, pos.y - radius * 0.68, radius * 1.4, radius * 1.36),
            -1.0,
            1.0,
            max(1, int(radius * 0.13)),
        )

    def draw_pci(self, now):
        data = SWING_TYPES[self.swing_mode]
        radius = data["pci"] * (0.92 + 0.04 * math.sin(now * 8.0))
        center = (int(self.pci_pos.x), int(self.pci_pos.y))
        draw_soft_circle(self.screen, center, radius * 0.78, (255, 222, 66, 50), 7)
        pygame.draw.circle(self.screen, (255, 218, 44), center, int(radius), 2)
        pygame.draw.circle(self.screen, (255, 246, 161), center, int(radius * 0.52), 1)
        pygame.draw.circle(self.screen, (255, 242, 126), center, 8)
        pygame.draw.circle(self.screen, (40, 35, 8), center, 3)
        for angle in (0, math.pi / 2, math.pi, math.pi * 1.5):
            inner = pygame.Vector2(math.cos(angle), math.sin(angle)) * (radius * 0.35)
            outer = pygame.Vector2(math.cos(angle), math.sin(angle)) * (radius * 0.64)
            pygame.draw.line(
                self.screen,
                (255, 235, 84),
                self.pci_pos + inner,
                self.pci_pos + outer,
                5,
            )

    def draw_bat_swing(self, now):
        age = 1.0 - clamp((self.swing_flash_until - now) / 0.28, 0.0, 1.0)
        side = -1 if self.swing_flash_kind != "power" else 1
        arc_rect = pygame.Rect(486, 544, 310, 180)
        start = math.radians(205 - age * 105) if side < 0 else math.radians(-25 + age * 105)
        end = start + math.radians(55)
        color = (224, 178, 101) if self.swing_flash_kind != "power" else (247, 205, 111)
        pygame.draw.arc(self.screen, color, arc_rect, start, end, 8)

    def draw_center_feedback(self, now):
        if not self.result_text and self.state == "pitch":
            return
        title = self.result_text
        sub = self.secondary_text
        if title:
            surf = self.fonts["big"].render(title, True, (246, 248, 252))
            shadow = self.fonts["big"].render(title, True, (18, 22, 30))
            rect = surf.get_rect(center=(WIDTH // 2, 116))
            self.screen.blit(shadow, rect.move(2, 2))
            self.screen.blit(surf, rect)
        if sub:
            surf = self.fonts["body"].render(sub, True, (222, 227, 235))
            rect = surf.get_rect(center=(WIDTH // 2, 158))
            self.screen.blit(surf, rect)

    def draw_field_view(self, now):
        self.draw_field_background()
        self.draw_field_lines()
        self.draw_runner_visuals(now)
        for fielder in self.fielders:
            self.draw_fielder(fielder, now)
        self.draw_throw_anim(now)
        if self.hit_ball:
            self.draw_hit_ball()
        self.draw_vignette()
        self.draw_center_feedback(now)

    def draw_field_background(self):
        draw_vertical_gradient(self.screen, (18, 94, 58), (18, 76, 48))
        for i in range(13):
            y = 112 + i * 46
            color = (25, 116, 66) if i % 2 == 0 else (20, 92, 57)
            pygame.draw.polygon(self.screen, color, [(0, y), (WIDTH, y - 34), (WIDTH, y + 12), (0, y + 46)])
        pygame.draw.rect(self.screen, (13, 23, 37), (0, 0, WIDTH, 118))
        pygame.draw.rect(self.screen, (109, 78, 55), (0, 118, WIDTH, 24))
        for x in range(0, WIDTH, 24):
            shade = 48 + x % 38
            pygame.draw.circle(self.screen, (shade, shade + 7, shade + 25), (x + 8, 74 + (x % 5)), 3)
        pygame.draw.arc(self.screen, (41, 129, 75), (-44, 86, WIDTH + 88, 970), math.radians(202), math.radians(338), 82)
        pygame.draw.arc(self.screen, (24, 61, 46), (-34, 116, WIDTH + 68, 910), math.radians(202), math.radians(338), 18)
        pygame.draw.polygon(
            self.screen,
            (157, 105, 64),
            [
                world_to_screen(0, 0),
                world_to_screen(90, 90),
                world_to_screen(0, 180),
                world_to_screen(-90, 90),
            ],
        )
        pygame.draw.polygon(
            self.screen,
            (100, 68, 45),
            [
                world_to_screen(0, 0),
                world_to_screen(90, 90),
                world_to_screen(0, 180),
                world_to_screen(-90, 90),
            ],
            3,
        )
        pygame.draw.circle(self.screen, (140, 92, 58), world_to_screen(0, 60), 25)
        pygame.draw.circle(self.screen, (96, 65, 43), world_to_screen(0, 60), 25, 2)

    def draw_field_lines(self):
        white = (226, 226, 210)
        pygame.draw.line(self.screen, white, world_to_screen(0, 0), world_to_screen(-430, 430), 3)
        pygame.draw.line(self.screen, white, world_to_screen(0, 0), world_to_screen(430, 430), 3)
        bases = [pygame.Vector2(90, 90), pygame.Vector2(0, 180), pygame.Vector2(-90, 90)]
        for base in bases:
            points = [
                world_to_screen(base.x, base.y + 5),
                world_to_screen(base.x + 5, base.y),
                world_to_screen(base.x, base.y - 5),
                world_to_screen(base.x - 5, base.y),
            ]
            pygame.draw.polygon(self.screen, (245, 244, 230), points)
        pygame.draw.polygon(
            self.screen,
            (245, 244, 230),
            [
                world_to_screen(0, -4),
                world_to_screen(7, 2),
                world_to_screen(0, 8),
                world_to_screen(-7, 2),
            ],
        )

    def draw_runner_visuals(self, now):
        if now < self.runner_anim_until:
            for anim in self.runner_anims:
                t = clamp((now - anim["start_time"]) / max(0.01, anim["end_time"] - anim["start_time"]), 0.0, 1.0)
                eased = smoothstep(t)
                world = anim["start"].lerp(anim["end"], eased)
                self.draw_runner(world, active=True, scored=anim["scored"])
        else:
            for index, occupied in enumerate(self.bases):
                if occupied:
                    self.draw_runner(base_world(index), active=False, scored=False)

    def draw_runner(self, world_pos, active=False, scored=False):
        p = world_to_screen(world_pos.x, world_pos.y, 0)
        color = (255, 225, 92) if active else (238, 242, 248)
        if scored:
            color = (114, 232, 148)
        pygame.draw.ellipse(self.screen, (9, 34, 24), (p.x - 8, p.y + 7, 16, 6))
        pygame.draw.circle(self.screen, (26, 34, 48), (int(p.x), int(p.y)), 8)
        pygame.draw.circle(self.screen, color, (int(p.x), int(p.y - 2)), 6)
        pygame.draw.circle(self.screen, (238, 210, 180), (int(p.x), int(p.y - 9)), 4)
        if active:
            pygame.draw.line(self.screen, color, (p.x - 4, p.y + 4), (p.x - 10, p.y + 10), 2)
            pygame.draw.line(self.screen, color, (p.x + 4, p.y + 4), (p.x + 10, p.y + 10), 2)

    def draw_throw_anim(self, now):
        if not self.throw_anim:
            return
        if now > self.throw_anim["end_time"]:
            self.throw_anim = None
            return
        t = clamp((now - self.throw_anim["start_time"]) / max(0.01, self.throw_anim["end_time"] - self.throw_anim["start_time"]), 0.0, 1.0)
        pos = self.throw_anim["start"].lerp(self.throw_anim["end"], smoothstep(t))
        start = world_to_screen(self.throw_anim["start"].x, self.throw_anim["start"].y, 4)
        end = world_to_screen(pos.x, pos.y, 5)
        pygame.draw.line(self.screen, (255, 244, 185), start, end, 2)
        draw_soft_circle(self.screen, (int(end.x), int(end.y)), 14, (255, 237, 156, 48), 4)
        pygame.draw.circle(self.screen, (255, 252, 240), (int(end.x), int(end.y)), 4)

    def draw_fielder(self, fielder, now):
        p = world_to_screen(fielder.pos.x, fielder.pos.y)
        stride = math.sin(now * 18.0) * clamp(fielder.running_speed / max(1.0, fielder.speed), 0.0, 1.0)
        glove = pygame.Vector2(-fielder.facing.y, fielder.facing.x) * 8 + pygame.Vector2(fielder.facing.x, -fielder.facing.y) * 4
        if fielder.action and now < fielder.action_until:
            if fielder.action == "catch":
                glove = pygame.Vector2(fielder.facing.x * 4, -18)
            elif fielder.action == "throw":
                glove = pygame.Vector2(-fielder.facing.x * 12, -14)
            elif fielder.action == "field":
                glove = pygame.Vector2(fielder.facing.x * 10, 5)
        pygame.draw.ellipse(self.screen, (8, 31, 22), (p.x - 12, p.y + 8, 24, 8))
        pygame.draw.line(self.screen, (24, 40, 70), (p.x - 4, p.y + 8), (p.x - 8 - stride * 4, p.y + 16), 3)
        pygame.draw.line(self.screen, (24, 40, 70), (p.x + 4, p.y + 8), (p.x + 8 + stride * 4, p.y + 16), 3)
        pygame.draw.circle(self.screen, (17, 31, 49), (int(p.x), int(p.y)), 11)
        pygame.draw.circle(self.screen, fielder.color, (int(p.x), int(p.y)), 9)
        pygame.draw.rect(self.screen, (235, 240, 248), (p.x - 6, p.y - 3, 12, 8), border_radius=3)
        pygame.draw.circle(self.screen, (143, 92, 52), (int(p.x + glove.x), int(p.y + glove.y)), 5)
        pygame.draw.circle(self.screen, (237, 220, 190), (int(p.x), int(p.y - 10)), 5)
        pygame.draw.circle(self.screen, (30, 45, 76), (int(p.x), int(p.y - 14)), 5)
        label = self.fonts["small"].render(fielder.name, True, (236, 240, 248))
        self.screen.blit(label, label.get_rect(center=(p.x, p.y + 21)))

    def draw_hit_ball(self):
        ball = self.hit_ball
        assert ball is not None
        for i, pt in enumerate(ball.trail):
            alpha = (i + 1) / len(ball.trail)
            p = world_to_screen(pt.x, pt.y, pt.z)
            pygame.draw.circle(self.screen, (int(255 * alpha), int(230 * alpha), int(145 * alpha)), p, max(1, int(2 + alpha * 3)))
        shadow = world_to_screen(ball.pos.x, ball.pos.y, 0)
        pygame.draw.ellipse(self.screen, (18, 49, 31), (shadow.x - 6, shadow.y - 2, 12, 5))
        p = world_to_screen(ball.pos.x, ball.pos.y, ball.pos.z)
        draw_soft_circle(self.screen, (int(p.x), int(p.y)), 20, (255, 226, 133, 46), 5)
        pygame.draw.circle(self.screen, (255, 252, 238), (int(p.x), int(p.y)), 6)
        pygame.draw.circle(self.screen, (178, 39, 44), (int(p.x - 2), int(p.y)), 2)
        self.last_hit_distance = ball.max_distance

    def draw_hud(self, now):
        if self.compact_ui:
            self.draw_compact_hud(now)
            self.draw_compact_hit_card(now)
            return
        self.draw_score_bug()
        self.draw_feedback_panel()
        self.draw_hit_card(now)
        self.draw_controls_strip()
        if self.state == "in_play":
            self.draw_in_play_panel()

    def draw_compact_hud(self, now):
        draw_glass_panel(self.screen, (18, 16, 316, 74), (7, 12, 21, 168), (255, 255, 255, 26), 9)
        score = self.fonts["score"].render(f"JIJ {self.player_score}  CPU {self.cpu_score}", True, (246, 249, 255))
        self.screen.blit(score, (34, 24))
        status = self.fonts["small"].render(
            f"Inning {self.inning}   Count {self.balls}-{self.strikes}   Outs {self.outs}",
            True,
            (213, 222, 235),
        )
        self.screen.blit(status, (36, 57))
        self.draw_bases(294, 52)

        draw_glass_panel(self.screen, (WIDTH - 356, 16, 338, 74), (7, 12, 21, 150), (255, 255, 255, 24), 9)
        pitch = self.fonts["small"].render(self.pitch_feedback, True, (230, 236, 246))
        timing_color = TIMING_COLORS.get(self.timing_feedback, (232, 236, 244))
        timing = self.fonts["body"].render(self.timing_feedback, True, timing_color)
        self.screen.blit(pitch, (WIDTH - 338, 25))
        self.screen.blit(timing, (WIDTH - 338, 51))
        hint = self.fonts["small"].render("H UI  F11 Fullscreen", True, (143, 156, 174))
        self.screen.blit(hint, hint.get_rect(center=(WIDTH // 2, HEIGHT - 22)))

    def draw_compact_hit_card(self, now):
        if now > self.hit_card_until or not self.last_hit_type:
            return
        w = 430
        h = 56
        x = WIDTH // 2 - w // 2
        y = 18
        draw_glass_panel(self.screen, (x, y, w, h), (7, 12, 21, 178), (255, 255, 255, 26), 9)
        title = self.fonts["bold"].render(self.last_hit_subtype or self.last_hit_type, True, (255, 232, 118))
        metrics = self.fonts["small"].render(
            f"EV {self.last_exit_velo:.0f} mph   LA {self.last_launch_angle:.0f}   {self.last_hit_distance:.0f} ft   PCI {self.last_pci_score * 100:.0f}%",
            True,
            (223, 231, 242),
        )
        self.screen.blit(title, (x + 16, y + 10))
        self.screen.blit(metrics, (x + 170, y + 20))

    def draw_score_bug(self):
        draw_glass_panel(self.screen, (20, 18, 340, 184), (8, 13, 23, 210), (255, 255, 255, 42), 10)

        title = self.fonts["score"].render(f"JIJ {self.player_score}  CPU {self.cpu_score}", True, (248, 250, 255))
        self.screen.blit(title, (38, 30))
        inning = self.fonts["body"].render(f"Inning {self.inning}", True, (205, 214, 226))
        self.screen.blit(inning, (39, 66))
        challenge = self.fonts["small"].render(f"Challenge {self.total_outs}/10 outs  Best {self.challenge_best}", True, (255, 226, 112))
        self.screen.blit(challenge, (38, 88))

        self.draw_count_dots("Balls", self.balls, 4, (79, 224, 132), 40, 116)
        self.draw_count_dots("Strikes", self.strikes, 3, (245, 188, 70), 40, 142)
        self.draw_count_dots("Outs", self.outs, 3, (255, 97, 97), 40, 168)
        self.draw_bases(292, 132)

    def draw_count_dots(self, label, count, total, color, x, y):
        text = self.fonts["small"].render(label, True, (205, 214, 226))
        self.screen.blit(text, (x, y - 10))
        for i in range(total):
            fill = color if i < count else (65, 72, 84)
            if i < count:
                draw_soft_circle(self.screen, (x + 88 + i * 20, y), 13, (*color, 42), 4)
            pygame.draw.circle(self.screen, fill, (x + 88 + i * 20, y), 7)
            pygame.draw.circle(self.screen, (255, 255, 255, 46), (x + 88 + i * 20, y), 7, 1)

    def draw_bases(self, cx, cy):
        points = [
            (cx, cy - 27),
            (cx + 26, cy),
            (cx, cy + 27),
            (cx - 26, cy),
        ]
        pygame.draw.polygon(self.screen, (57, 64, 74), points, 2)
        base_positions = [(cx + 25, cy), (cx, cy - 25), (cx - 25, cy)]
        for occupied, pos in zip(self.bases, base_positions):
            color = (255, 225, 105) if occupied else (42, 48, 58)
            diamond = [(pos[0], pos[1] - 8), (pos[0] + 8, pos[1]), (pos[0], pos[1] + 8), (pos[0] - 8, pos[1])]
            pygame.draw.polygon(self.screen, color, diamond)
            pygame.draw.polygon(self.screen, (229, 235, 245), diamond, 1)

    def draw_feedback_panel(self):
        x = WIDTH - 366
        y = 18
        draw_glass_panel(self.screen, (x, y, 346, 168), (8, 13, 23, 196), (255, 255, 255, 38), 10)

        pitch = self.pitch_feedback
        timing_color = TIMING_COLORS.get(self.timing_feedback, (232, 236, 244))
        lines = [
            ("Pitch", pitch, (232, 236, 244)),
            ("Timing", self.timing_feedback, timing_color),
            ("Contact", self.contact_feedback or self.last_hit_type or "-", (255, 232, 132)),
            ("Swing", SWING_TYPES[self.swing_mode]["label"], (215, 224, 238)),
        ]
        for idx, (label, value, color) in enumerate(lines):
            yy = y + 20 + idx * 34
            left = self.fonts["small"].render(label.upper(), True, (142, 153, 170))
            right = self.fonts["body"].render(value, True, color)
            self.screen.blit(left, (x + 18, yy + 5))
            self.screen.blit(right, (x + 106, yy))

    def draw_hit_card(self, now):
        if now > self.hit_card_until or not self.last_hit_type:
            return
        x = WIDTH - 366
        y = 202
        draw_glass_panel(self.screen, (x, y, 346, 142), (8, 13, 23, 200), (255, 255, 255, 38), 10)
        title = self.fonts["bold"].render(self.last_hit_subtype or self.last_hit_type, True, (255, 232, 118))
        self.screen.blit(title, (x + 18, y + 14))
        metrics = [
            ("EV", f"{self.last_exit_velo:.0f} mph"),
            ("LA", f"{self.last_launch_angle:.0f} deg"),
            ("DIST", f"{self.last_hit_distance:.0f} ft"),
            ("PCI", f"{self.last_pci_score * 100:.0f}%"),
            ("TIMING", self.timing_feedback.replace(" Timing", "")),
            ("QUALITY", f"{self.last_contact_quality * 100:.0f}%"),
        ]
        for i, (label, value) in enumerate(metrics):
            col = i % 2
            row = i // 2
            mx = x + 18 + col * 158
            my = y + 56 + row * 26
            lab = self.fonts["small"].render(label, True, (138, 151, 169))
            val = self.fonts["small"].render(value, True, (232, 238, 248))
            self.screen.blit(lab, (mx, my))
            self.screen.blit(val, (mx + 64, my))

    def draw_controls_strip(self):
        text = "Muis: PCI   Linksklik/Space: normal   Rechtermuisknop/X: power   Shift+klik/C: contact   R: reset"
        surf = self.fonts["small"].render(text, True, (216, 224, 236))
        rect = pygame.Rect(0, 0, surf.get_width() + 36, 36)
        rect.center = (WIDTH // 2, HEIGHT - 24)
        draw_glass_panel(self.screen, rect, (8, 13, 23, 172), (255, 255, 255, 30), 9)
        self.screen.blit(surf, surf.get_rect(center=rect.center))

    def draw_in_play_panel(self):
        x = 20
        y = HEIGHT - 124
        draw_glass_panel(self.screen, (x, y, 286, 92), (8, 13, 23, 202), (255, 255, 255, 38), 10)
        dist = self.fonts["bold"].render(f"Afstand {self.last_hit_distance:.0f} ft", True, (255, 236, 120))
        self.screen.blit(dist, (x + 18, y + 16))
        metric = self.fonts["small"].render(f"{self.last_hit_distance * 0.3048:.0f} meter | {self.last_hit_type}", True, (218, 226, 238))
        self.screen.blit(metric, (x + 18, y + 54))


def main():
    pygame.mixer.pre_init(44100, -16, 1, 512)
    game = Game()
    game.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pygame.quit()
        sys.exit(0)
