import cv2
import os
import tifffile # for reading tiff files. There are other modules that can do this, but tifffile is most stable on Windows
import numpy as np # for array handling
import matplotlib.pyplot as plt # for QC
import glob # to gather up image filepath lists
import rasterio
from rasterio.plot import show
import scipy # same
import imagecodecs
from sklearn.model_selection import train_test_split
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score
from skimage.transform import resize
from skimage.util import random_noise
from tensorflow.keras.models import Model, load_model
from tensorflow.keras.layers import Input, BatchNormalization, Activation, Dense, Dropout
from tensorflow.keras.layers import Lambda, RepeatVector, Reshape
from tensorflow.keras.layers import concatenate, add
from tensorflow.keras.layers import Conv2D, Conv2DTranspose,MaxPooling2D, UpSampling2D
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint, ReduceLROnPlateau, Callback
from tensorflow.keras.optimizers import Adam
from tensorflow.keras import regularizers
from tensorflow.keras.preprocessing.image import ImageDataGenerator, array_to_img, img_to_array, load_img
from tensorflow.keras import backend as K
# We need some additional layers already pre-defined in Keras



# creating a function for reading the training data
def read_data(files_path):
    top_list = glob.glob(files_path)
    top_list = np.sort(top_list)
    # print(top_list)
    return top_list

# Function to Create arrays for resizing
def create_arrays(Image_resize_value:int, Number_of_channels:int):
    imgResize = Image_resize_value
    channels = Number_of_channels

    # Define the directory where you want to save the individual images
    save_dir_train = './train_images/'
    save_dir_test = './test_images/'

    # Create directories if they don't exist
    os.makedirs(save_dir_train, exist_ok=True)
    os.makedirs(save_dir_test, exist_ok=True)

    return save_dir_train, save_dir_test

# Function to resize the images
def resize_images(resize_value, no_of_channels, data_list, output_file_path):

    for i in range(len(data_list)):
        img0 = tifffile.imread(data_list[i])  # Read the image
        img_reshaped = cv2.resize(img0, (resize_value, no_of_channels))  # Resize it

        # Local normalization & standardization of the image values
        img_norm = np.clip((img_reshaped - img_reshaped.mean()) / (0.5 * img_reshaped.std()), 0, 1)

        # Save the individual reshaped image as TIFF
        save_path = os.path.join(output_file_path, f'image_{i}.tif')
        tifffile.imwrite(save_path, img_norm)

def plot_label(label_list_path, index_number):
    img = tifffile.imread(label_list_path[index_number])
    plt.imshow(img, cmap='Blues')

# Function to create directories
def create_dir(directory_path):
    os.makedirs(directory_path, exist_ok=True)

# Function for creation of onehot labels
def onehot_label(label_total, label_list, image_resize_value, size):
    onehot_label_total = np.zeros((len(label_list),image_resize_value,image_resize_value,size), dtype=float)
    for k in range(len(label_list)):
        for i in range(image_resize_value):
            for j in range(image_resize_value):
                # ocean
                if label_total[k,i,j,0]==0.:
                    onehot_label_total[k,i,j,0]=1
                # ice sheet
                elif label_total[k,i,j,0]>0:
                    onehot_label_total[k,i,j,1]=1

    return onehot_label_total

# Function to standarize the dataset
def standardize_data(data_files):
    data_scaled = np.zeros(np.shape(data_files))
    data_normalized = np.zeros((np.shape(data_files)))
    for i in range(np.shape(data_files)[-1]):
        data_mean = np.mean(data_files[:,:,:,i])
        data_std = np.std(data_files[:,:,:,i])
        data_scaled[:,:,:,i] = (data_files[:,:,:,i]-data_mean)/data_std
        # Keep it in the positve range?
        data_scaled[:,:,:,i] = np.clip((data_scaled[:,:,:,i]+1.0)/2.0,0,1)

    return data_scaled

# Function for resizing the labels and converting them to the array
def label_list_to_array(label_list, image_resize_value, size):
    label_total = np.zeros((len(label_list), image_resize_value, image_resize_value, size))
    for i in range(len(label_list)):
        img = tifffile.imread(label_list[i])
        img_reshaped = resize(img, (image_resize_value, image_resize_value, 1))
        label_total[i] = img_reshaped
    return label_total

# TRAINING THE DL MODEL
def f1(y_true, y_pred):
    def recall(y_true, y_pred):
        """Recall metric.

        Only computes a batch-wise average of recall.

        Computes the recall, a metric for multi-label classification of
        how many relevant items are selected.
        """
        true_positives = K.sum(K.round(K.clip(y_true * y_pred, 0, 1)))
        possible_positives = K.sum(K.round(K.clip(y_true, 0, 1)))
        recall = true_positives / (possible_positives + K.epsilon())
        return recall

    def precision(y_true, y_pred):
        """Precision metric.

        Only computes a batch-wise average of precision.

        Computes the precision, a metric for multi-label classification of
        how many selected items are relevant.
        """
        true_positives = K.sum(K.round(K.clip(y_true * y_pred, 0, 1)))
        predicted_positives = K.sum(K.round(K.clip(y_pred, 0, 1)))
        precision = true_positives / (predicted_positives + K.epsilon())
        return precision
    precision = precision(y_true, y_pred)
    recall = recall(y_true, y_pred)
    return 2*((precision*recall)/(precision+recall+K.epsilon()))

def dice_coef(y_true, y_pred, smooth=1):
    """
    Dice = (2*|X & Y|)/ (|X|+ |Y|)
         =  2*sum(|A*B|)/(sum(A^2)+sum(B^2))
    ref: https://arxiv.org/pdf/1606.04797v1.pdf
    """
    intersection = K.sum(K.abs(y_true * y_pred), axis=-1)
    return (2. * intersection + smooth) / (K.sum(K.square(y_true),-1) + K.sum(K.square(y_pred),-1) + smooth)

def dice_coef_loss(y_true, y_pred):
    return 1-dice_coef(y_true, y_pred)

def jaccard_distance_loss(y_true, y_pred, smooth=100):
    """
    Jaccard = (|X & Y|)/ (|X|+ |Y| - |X & Y|)
            = sum(|A*B|)/(sum(|A|)+sum(|B|)-sum(|A*B|))
    
    The jaccard distance loss is usefull for unbalanced datasets. This has been
    shifted so it converges on 0 and is smoothed to avoid exploding or disapearing
    gradient.
    
    Ref: https://en.wikipedia.org/wiki/Jaccard_index
    
    @url: https://gist.github.com/wassname/f1452b748efcbeb4cb9b1d059dce6f96
    @author: wassname
    """
    intersection = K.sum(K.abs(y_true * y_pred), axis=-1)
    sum_ = K.sum(K.abs(y_true) + K.abs(y_pred), axis=-1)
    jac = (intersection + smooth) / (sum_ - intersection + smooth)
    return (1 - jac) * smooth

# Define Neural Network
#https://github.com/karolzak/keras-unet
def bn_conv_relu(input, filters, bachnorm_momentum, **conv2d_args):
    x = BatchNormalization(momentum=bachnorm_momentum)(input)
    x = Conv2D(filters, **conv2d_args)(x)
    return x

def bn_upconv_relu(input, filters, bachnorm_momentum, **conv2d_trans_args):
    x = BatchNormalization(momentum=bachnorm_momentum)(input)
    x = Conv2DTranspose(filters, **conv2d_trans_args)(x)
    return x

def satellite_unet(
    input_shape,
    num_classes=1,
    output_activation='sigmoid',
    num_layers=4):

    inputs = Input(input_shape)   
    
    filters = 64
    upconv_filters = 96

    kernel_size = (3,3)
    activation = 'relu'
    strides = (1,1)
    padding = 'same'
    kernel_initializer = 'he_normal'

    conv2d_args = {
        'kernel_size':kernel_size,
        'activation':activation, 
        'strides':strides,
        'padding':padding,
        'kernel_initializer':kernel_initializer
        }

    conv2d_trans_args = {
        'kernel_size':kernel_size,
        'activation':activation, 
        'strides':(2,2),
        'padding':padding,
        'output_padding':(1,1)
        }

    bachnorm_momentum = 0.01

    pool_size = (2,2)
    pool_strides = (2,2)
    pool_padding = 'valid'

    maxpool2d_args = {
        'pool_size':pool_size,
        'strides':pool_strides,
        'padding':pool_padding,
        }
    
    x = Conv2D(filters, **conv2d_args)(inputs)
    c1 = bn_conv_relu(x, filters, bachnorm_momentum, **conv2d_args)    
    x = bn_conv_relu(c1, filters, bachnorm_momentum, **conv2d_args)
    x = MaxPooling2D(**maxpool2d_args)(x)

    down_layers = []

    for l in range(num_layers):
        x = bn_conv_relu(x, filters, bachnorm_momentum, **conv2d_args)
        x = bn_conv_relu(x, filters, bachnorm_momentum, **conv2d_args)
        down_layers.append(x)
        x = bn_conv_relu(x, filters, bachnorm_momentum, **conv2d_args)
        x = MaxPooling2D(**maxpool2d_args)(x)

    x = bn_conv_relu(x, filters, bachnorm_momentum, **conv2d_args)
    x = bn_conv_relu(x, filters, bachnorm_momentum, **conv2d_args)
    x = bn_upconv_relu(x, filters, bachnorm_momentum, **conv2d_trans_args)

    for conv in reversed(down_layers):        
        x = concatenate([x, conv])  
        x = bn_conv_relu(x, upconv_filters, bachnorm_momentum, **conv2d_args)
        x = bn_conv_relu(x, filters, bachnorm_momentum, **conv2d_args)
        x = bn_upconv_relu(x, filters, bachnorm_momentum, **conv2d_trans_args)

    x = concatenate([x, c1])
    x = bn_conv_relu(x, upconv_filters, bachnorm_momentum, **conv2d_args)
    x = bn_conv_relu(x, filters, bachnorm_momentum, **conv2d_args)
           
    outputs = Conv2D(num_classes, kernel_size=(1,1), strides=(1,1), activation=output_activation, padding='valid') (x)       
    
    model = Model(inputs=[inputs], outputs=[outputs])
    return model

# For plotting the results
def plot_sample_all(X, y, preds, binary_preds, ix=None, filename='Sample.png'):
    import matplotlib

    matplotlib.rc('xtick', labelsize=20) 
    matplotlib.rc('ytick', labelsize=20) 
    if ix is None:
        ix = random.randint(0, len(X))

    has_mask = y[ix].max() > 0

    fig, ax = plt.subplots(3, 2, sharex=True, sharey=True, figsize=(20, 10))
    r_band = (X[ix,:,:,0]-np.min(X[ix,:,:,0]))/(np.max(X[ix,:,:,0])-np.min(X[ix,:,:,0]))
    g_band = (X[ix,:,:,1]-np.min(X[ix,:,:,1]))/(np.max(X[ix,:,:,1])-np.min(X[ix,:,:,1]))
    b_band = (X[ix,:,:,2]-np.min(X[ix,:,:,2]))/(np.max(X[ix,:,:,2])-np.min(X[ix,:,:,2]))
    RGB = np.stack((r_band, g_band, b_band), axis=-1)

    im0 = ax[0,0].imshow(RGB)
    #if has_mask:
        #ax[0].contour(y[ix].squeeze(), colors='k', levels=[0.5])

    ax[0,0].set_title('Remote Sensing Image', fontsize=30)
    
        
    im1 = ax[0,1].imshow(X[ix,:,:,0].squeeze(), cmap='gray')

    ax[0,1].set_title('Green', fontsize=30)
    
    im2 = ax[1,0].imshow(X[ix,:,:,2].squeeze(), cmap='gray')

    ax[1,0].set_title('NIR', fontsize=30)
    
    total_mask = np.zeros((512, 512, 3))
    for i in range(512):
        for j in range(512):
            # Ocean
            if(y[ix,i,j,0]==1):
                total_mask[i,j,0]=1
                total_mask[i,j,1]=1
                total_mask[i,j,2]=1
            # Ice
            elif(y[ix,i,j,1]==1):
                total_mask[i,j,0]=0
                total_mask[i,j,1]=0
                total_mask[i,j,2]=1


                
    im3 = ax[1,1].imshow(total_mask)
    ax[1,1].set_title('Image Mask', fontsize=30)
    
    im4 = ax[2,0].imshow(binary_preds[ix,:,:,0].squeeze(), vmin=0, vmax=1)

    ax[2,0].set_title('Ocean (Binary)', fontsize=30)
    
    im5 = ax[2,1].imshow(binary_preds[ix,:,:,1].squeeze(), vmin=0, vmax=1)

    ax[2,1].set_title('Ice (Binary)', fontsize=30)
    
    plt.xticks(fontsize=20)
    plt.yticks(fontsize=20)
    
    plt.tick_params(axis='both', which='major', labelsize=22)
    fig.tight_layout();
    plt.savefig(filename)

# Plotting the Predictions 
def plot_sample(X, y, preds, binary_preds, ix=None):
    if ix is None:
        ix = random.randint(0, len(X))

    has_mask = y[ix].max() > 0

    fig, ax = plt.subplots(2, 2, sharex=True, sharey=True, figsize=(20, 10))
    band = (X[ix,:,:,0]-np.min(X[ix,:,:,0]))/(np.max(X[ix,:,:,0])-np.min(X[ix,:,:,0]))
    #print(np.shape(RGB))
    im0 = ax[0,0].imshow(band.squeeze(), cmap='gray')
    #if has_mask:
        #ax[0].contour(y[ix].squeeze(), colors='k', levels=[0.5])
    fig.colorbar(im0, ax=ax[0,0], fraction=0.046, pad=0.04)
    ax[0,0].set_title('Remote Sensing Image')

    im1 = ax[0,1].imshow(y[ix,:,:,1].squeeze(), vmin=0, vmax=1)
    ax[0,1].set_title('Lable Glacier')
    fig.colorbar(im1, ax=ax[0,1],fraction=0.046, pad=0.04)
    
    im2 = ax[1,0].imshow(preds[ix,:,:,1].squeeze(), vmin=0, vmax=1)
    #if has_mask:
        #ax[2].contour(y[ix].squeeze(), colors='k', levels=[0.5])
    fig.colorbar(im2, ax=ax[1,0], fraction=0.046, pad=0.04)
    ax[1,0].set_title('Ice Predicted')
    
    im3 = ax[1,1].imshow(binary_preds[ix,:,:,1].squeeze(), vmin=0, vmax=1)
    #if has_mask:
        #ax[3].contour(y[ix].squeeze(), colors='k', levels=[0.5])
    fig.colorbar(im3, ax=ax[1,1], fraction=0.046, pad=0.04)
    ax[1,1].set_title('Ice Predicted (Binary)')
    fig.tight_layout();

# Function for calculating the accuracy metrics
def print_roc_metrics(y_real, y_predict):

    c_matrix = confusion_matrix(y_real.ravel(), y_predict.ravel())
    f1 = f1_score(y_real.ravel(), y_predict.ravel())
    recall = recall_score(y_real.ravel(), y_predict.ravel())
    precision = precision_score(y_real.ravel(), y_predict.ravel())
    print("Confusion matrix:")
    print(c_matrix)
    print("F1 score: {:.4f}".format(f1))
    print("Recall score: {:.4f}".format(recall))
    print("Precision score: {:.4f}".format(precision))