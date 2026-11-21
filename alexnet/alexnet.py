import random #Different starting weights let the 96 filters develop different responses during training.

red = 128.0
green = 64.0
blue = 32.0

pixel = [red, green, blue]

image_size = 27
image = [
[pixel.copy() for _ in range(image_size)] 
for _ in range(image_size)
]

image[0][0][0] = 255.0

height = len(image)
width = len(image[0])
channels = len(image[0][0])

kernel_size = 11 #names the height and width of a square filter. 11 means the filter covers 11 rows × 11 columns of pixels at each position.

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

feature_maps = []
random.seed(0) #Make our random starting weights repeatable
for filter_index in range(numFilters):
    weight = random.gauss(0.0, 0.01)
    filter_weights = [weight] #[weight] creates a list whose first entry is the random weight already generated.
    weights_per_filter = kernel_size * kernel_size * channels

    for _ in range(weights_per_filter - 1): #We use '_' because we need the repetitions but won’t use the individual numbers. weights_per_filter - 1 gives 362 repetitions because the list already holds one weight.
        filter_weights.append(random.gauss(0.0, 0.01)) #random.gauss(0.0, 0.01) supplies a fresh weight on each iteration, using the same mean (0.0) and standard deviation (0.01) as the first weight.

    filter_bias = 0.0 #names the trainable value this filter adds to its weighted sum at every image position. 0.0 matches AlexNet’s initialization for the first convolutional layer.
    feature_map = [[0.0] * output_width for _ in range(output_height)]
    for output_row in range(output_height):  #The output row index tells us where to store the result.
        start_row = output_row * stride
        for output_col in range(output_width):
            start_col = output_col * stride
            response = filter_bias
            weight_index = 0 #weight_index tracks the next entry in the filter’s flat list of weights. = 0 starts at the first weight every time we begin a new output cell.
            for input_row in range(start_row, start_row + kernel_size):
                for input_col in range(start_col, start_col + kernel_size):
                    for channel in range(channels): 
                        response += image[input_row][input_col][channel] * filter_weights[weight_index] #image[start_row][start_col][channel] reads one color value from the current patch’s top-left pixel.
                        weight_index += 1 #weight_index tracks the next weight.
            feature_map[output_row][output_col] = max(0.0, response) #this is ReLU

    feature_maps.append(feature_map)

normalization_radius = 2
normalization_k = 2.0
normalization_alpha = 0.0001
normalization_beta = 0.75
normalized_maps = []

for channel_index in range(len(feature_maps)):
    neighbor_start = max(0, channel_index - normalization_radius)
    neighbor_stop = min(len(feature_maps), channel_index + normalization_radius + 1)
    normalized_map = [[0.0] * output_width for _ in range(output_height)]
    for output_row in range(output_height): 
        for output_col in range (output_width):
            sum_of_squares = 0.0
            for neighbor_index in range(neighbor_start, neighbor_stop):
                neighbor_response = feature_maps[neighbor_index][output_row][output_col]
                sum_of_squares += neighbor_response ** 2
            normalization_base = normalization_k + normalization_alpha * sum_of_squares
            normalization_divisor = normalization_base ** normalization_beta
            normalized_map[output_row][output_col] = feature_maps[channel_index][output_row][output_col] / normalization_divisor
    normalized_maps.append(normalized_map)

print(feature_maps[0][0][0], normalized_maps[0][0][0]) #len(feature_maps) counts the filters’ grids. len(feature_maps[0]) counts rows in the first grid. len(feature_maps[0][0]) counts columns in its first row.
pool_size = 3
pool_stride = 2
pooled_height = (output_height - pool_size) // pool_stride + 1
pooled_width = (output_width - pool_size) // pool_stride + 1
pooled_maps = []

for normalized_map in normalized_maps:
    pooled_map = [[0.0] * pooled_width for _ in range(pooled_height)]
    for pooled_row in range(pooled_height):
        pool_start_row = pooled_row * pool_stride
        for pooled_col in range(pooled_width):

#Reproducing the architecture is one milestone. Reproducing the paper’s accuracy will also take ImageNet preprocessing, training, and evaluation--and we will go there, one little step at a time.