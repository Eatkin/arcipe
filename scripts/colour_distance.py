from garde.models import COLOUR_MAX
from garde.models import COLOUR_RGB
from garde.models import colour_distance


for colour in COLOUR_RGB:
    dist = colour_distance(colour, "green") / COLOUR_MAX
    print(f"  {colour:<10} → {dist:.2f}")
