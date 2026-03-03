import numpy as np
import sys
from PIL import Image

try:
    # Look for the image in the current directory or nearby
    import os
    img_path = None
    for f in os.listdir('.'):
        if f.endswith('.png') or f.endswith('.jpg') or f.endswith('.jpeg'):
            img_path = f
            break
    if not img_path:
        # Check /tmp
        for f in os.listdir('/tmp'):
            if f.endswith('.png'):
                img_path = os.path.join('/tmp', f)
                break
    
    if img_path:
        img = Image.open(img_path).convert('L')
        arr = np.array(img)
        print("Image shape:", arr.shape)
        # Just print a downsampled ascii version of the middle part
        for r in range(0, arr.shape[0], max(1, arr.shape[0]//50)):
            line = ""
            for c in range(0, arr.shape[1], max(1, arr.shape[1]//100)):
                if arr[r,c] < 150:
                    line += "#"
                else:
                    line += "."
            print(line)
except Exception as e:
    print("Error:", e)
