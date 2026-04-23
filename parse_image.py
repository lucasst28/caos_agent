from PIL import Image
import numpy as np

img = Image.open('1.png').convert('L')
img = img.resize((img.width // 2, img.height // 2))
arr = np.array(img)
for r in range(0, arr.shape[0], 10):
    line = ""
    for c in range(0, arr.shape[1], 5):
        if arr[r,c] < 128:
            line += "#"
        else:
            line += " "
    print(line)
