import random #Prepare to give the filters different initial weights
#Different starting weights let the 96 filters develop different responses during training.

red = 128.0
green = 64.0
blue = 32.0

pixel = [red, green, blue]

image = [
[pixel.copy() for _ in range(15)] #The inner loop runs pixel.copy() 15 times to make one row of 15 pixels.
    for _ in range(15) #The outer loop repeats that whole row-building operation 15 times to make 15 rows.
]

height = len(image)
width = len(image[0])
channels = len(image[0][0])

kernel_size = 11 #names the height and width of a square filter. 11 means the filter covers 11 rows × 11 columns of pixels at each position.
#With our 3 color channels, each filter will contain 11 × 11 × 3 = 363 weights. Training will adjust those weights.

#Set how far the filter moves between positions, measured in pixels
stride = 4 #4 means moving four pixels horizontally between positions, and four pixels vertically between rows of positions.
#The filter still covers 11 × 11 pixels, so neighboring windows overlap. A larger stride evaluates fewer positions, reducing computation and output size.

output_height = (height - kernel_size) // stride + 1 #height - kernel_size gives the last row where the filter can start. Your image has 15 rows and the filter covers 11, so the last start is row 4. // stride counts how many whole steps of 4 pixels fit. Here, the filter can take one step down. + 1 counts the initial position at row 0, before any step.
output_width =  (width - kernel_size) //stride + 1 #width - kernel_size gives 15 - 11 = 4. That’s how far the filter’s left edge can move before its right edge goes past the image. // stride counts the full jumps of four pixels that fit. Here, there’s one.
numFilters = 96 #96 is AlexNet’s chosen count for this layer.
#Each filter has its own 363 weights and one bias. Applying one filter across the image produces one feature map.
#A feature map is a representation of the image

#The weights learn which pattern to detect. The bias adjusts the threshold for responding to it.
#96 filters produce 96 output channels, allowing the layer to learn different patterns. An output channel is one feature map produced by a convolutional layer.
#A convolution extracts features from small neighborhoods of pixels.

random.seed(0) #Make our random starting weights repeatable
weight = random.gauss(0.0, 0.01)
filter_weights = [weight] #[weight] creates a list whose first entry is the random weight already generated.
weights_per_filter = kernel_size * kernel_size * channels

for _ in range(weights_per_filter - 1): #We use '_' because we need the repetitions but won’t use the individual numbers. weights_per_filter - 1 gives 362 repetitions because the list already holds one weight.
    filter_weights.append(random.gauss(0.0, 0.01)) #random.gauss(0.0, 0.01) supplies a fresh weight on each iteration, using the same mean (0.0) and standard deviation (0.01) as the first weight.

filter_bias = 0.0 #names the trainable value this filter adds to its weighted sum at every image position. 0.0 matches AlexNet’s initialization for the first convolutional layer.
response = filter_bias
for channel in range(channels): 
    response += image[0][0][channel] * filter_weights[channel] #image[0][0][channel] selects the current color value from the top-left pixel. filter_weights[channel] uses the same index to select its matching weight.


print(response)
