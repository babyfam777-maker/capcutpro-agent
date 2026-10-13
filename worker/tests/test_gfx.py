from app import gfx


def test_headline_is_white_red_and_not_gold():
    image = gfx.headline_sprite("WAIT FOR IT", "WAIT", 100)
    assert image.mode == "RGBA"
    assert image.width > 40
    pixels = image.getdata()
    red = white = 0
    for r, g, b, a in pixels:
        if a < 40:
            continue
        if r > 200 and g < 90 and b < 90:
            red += 1
        if r > 220 and g > 220 and b > 220:
            white += 1
    assert red > 20
    assert white > 20
    assert gfx.contains_gold(image) is False


def test_caption_has_one_accent_word():
    sprites = gfx.caption_sprites("look AGAIN now", "again", 72)
    assert len(sprites) == 3
    # The middle word is the red accent.
    assert gfx.contains_gold(sprites[1]) is False
