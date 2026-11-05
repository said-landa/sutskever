import random #Prepare to give the filters different initial weights
#Different starting weights let the 96 filters develop different responses during training.

red = 128.0
green = 64.0
blue = 32.0

pixel = [red, green, blue]

image = [
[pixel]
]

height = len(image)
widht = len(image[0])
channels = len(image[0][0])

filterSize = 11 #names the height and width of a square filter. 11 means the filter covers 11 rows × 11 columns of pixels at each position.
#With our 3 color channels, each filter will contain 11 × 11 × 3 = 363 weights. Training will adjust those weights.

#Set how far the filter moves between positions, measured in pixels
stride = 4 #4 means moving four pixels horizontally between positions, and four pixels vertically between rows of positions.
#The filter still covers 11 × 11 pixels, so neighboring windows overlap. A larger stride evaluates fewer positions, reducing computation and output size.

numFilters = 96 #96 is AlexNet’s chosen count for this layer.
#Each filter has its own 363 weights and one bias. Applying one filter across the image produces one feature map.
#A feature map is a representation of the image

#The weights learn which pattern to detect. The bias adjusts the threshold for responding to it.
#96 filters produce 96 output channels, allowing the layer to learn different patterns. An output channel is one feature map produced by a convolutional layer.
#A convolution extracts features from small neighborhoods of pixels.

random.seed(0) #Make our random starting weights repeatable

print(image)