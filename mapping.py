"""
Art-Net universe mapping helpers. No GUI, no tkinter. Shared by main.py and plsq.py.
"""


def effective_chips_per_universe(setting):
    """Return effective chips per universe: 128 when setting is 0 (auto), else min(setting, 128)."""
    if setting == 0:
        return 128
    return min(int(setting), 128)


def build_spans(start_universe, start_channel, channels_per_chip, chips, chips_per_universe=0):
    """Lay chips out over universes.

    Returns [(universe, first_channel0, channel_count, offset_in_frame)].
    """
    if channels_per_chip < 1 or channels_per_chip > 512:
        raise ValueError("Channels per chip must be between 1 and 512")
    if not 1 <= start_channel <= 512:
        raise ValueError("Start channel must be between 1 and 512")
    cap = 512 // channels_per_chip
    per_u = min(chips_per_universe, cap) if chips_per_universe > 0 else cap
    spans, placed, uni, ch = [], 0, start_universe, start_channel - 1
    while placed < chips:
        fit = min(per_u, (512 - ch) // channels_per_chip)
        if fit <= 0:
            raise ValueError(
                "Not a single chip fits into the universe: lower the start channel"
            )
        n = min(fit, chips - placed)
        spans.append((uni, ch, n * channels_per_chip, placed * channels_per_chip))
        placed += n
        uni += 1
        ch = 0
    return spans


def count_universes(start_universe, start_channel, chips_per_universe, chips):
    """Number of universes needed for a given RGBW layout (always 4 channels per chip)."""
    eff = effective_chips_per_universe(chips_per_universe)
    spans = build_spans(start_universe, start_channel, 4, chips, eff)
    return len(spans)
