from arcipe.models import COLOUR_RGB, COLOUR_MAX, colour_distance

for colour in COLOUR_RGB:
    dist = colour_distance(colour, "green") / COLOUR_MAX
    print(f"  {colour:<10} → {dist:.2f}")
