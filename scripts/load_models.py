#!/usr/bin/env python
# Copyright (c) 2022 Lincoln D. Stein (https://github.com/lstein)
# Before running stable-diffusion on an internet-isolated machine,
# run this script from one with internet connectivity. The
# two machines must share a common .cache directory.
#
# Coauthor: Kevin Turner http://github.com/keturn
#
print('Loading Python libraries...\n')
import argparse
import sys
import os
import re
import warnings
import shutil
from urllib import request
from tqdm import tqdm
from omegaconf import OmegaConf
from huggingface_hub import HfFolder, hf_hub_url
from pathlib import Path
from getpass_asterisk import getpass_asterisk
from transformers import CLIPTokenizer, CLIPTextModel
from ldm.invoke.globals import Globals

import traceback
import requests
import clip
import transformers
import warnings
warnings.filterwarnings('ignore')
import torch
transformers.logging.set_verbosity_error()

#--------------------------globals--
Model_dir = 'models'
Weights_dir = 'ldm/stable-diffusion-v1/'
Default_config_file = './configs/models.yaml'
SD_Configs = './configs/stable-diffusion'
Datasets = {
    'stable-diffusion-1.5':  {
        'description': 'The newest Stable Diffusion version 1.5 weight file (4.27 GB)',
        'repo_id': 'runwayml/stable-diffusion-v1-5',
        'config': 'v1-inference.yaml',
        'file': 'v1-5-pruned-emaonly.ckpt',
        'recommended': True,
        'width': 512,
        'height': 512,
    },
    'inpainting-1.5': {
        'description': 'RunwayML SD 1.5 model optimized for inpainting (4.27 GB)',
        'repo_id': 'runwayml/stable-diffusion-inpainting',
        'config': 'v1-inpainting-inference.yaml',
        'file': 'sd-v1-5-inpainting.ckpt',
        'recommended': True,
        'width': 512,
        'height': 512,
    },
    'stable-diffusion-1.4': {
        'description': 'The original Stable Diffusion version 1.4 weight file (4.27 GB)',
        'repo_id': 'CompVis/stable-diffusion-v-1-4-original',
        'config': 'v1-inference.yaml',
        'file': 'sd-v1-4.ckpt',
        'recommended': False,
        'width': 512,
        'height': 512,
    },
    'waifu-diffusion-1.3': {
        'description': 'Stable Diffusion 1.4 fine tuned on anime-styled images (4.27)',
        'repo_id': 'hakurei/waifu-diffusion-v1-3',
        'config': 'v1-inference.yaml',
        'file': 'model-epoch09-float32.ckpt',
        'recommended': False,
        'width': 512,
        'height': 512,
    },
    'ft-mse-improved-autoencoder-840000': {
        'description': 'StabilityAI improved autoencoder fine-tuned for human faces (recommended; 335 MB)',
        'repo_id': 'stabilityai/sd-vae-ft-mse-original',
        'config': 'VAE',
        'file': 'vae-ft-mse-840000-ema-pruned.ckpt',
        'recommended': True,
        'width': 512,
        'height': 512,
    },
}
Config_preamble = '''# This file describes the alternative machine learning models
# available to InvokeAI script.
#
# To add a new model, follow the examples below. Each
# model requires a model config file, a weights file,
# and the width and height of the images it
# was trained on.
'''

#---------------------------------------------
def introduction():
    print(
        '''Welcome to InvokeAI. This script will help download the Stable Diffusion weight files
and other large models that are needed for text to image generation. At any point you may interrupt
this program and resume later.\n'''
    )

#--------------------------------------------
def postscript():
    print(
        '''\n** Model Installation Successful **\nYou're all set! You may now launch InvokeAI using one of these two commands:
Web version: 
    python scripts/invoke.py --web  (connect to http://localhost:9090)
Command-line version:
   python scripts/invoke.py

Remember to activate that 'invokeai' environment before running invoke.py.

Or, if you used one of the automated installers, execute "invoke.sh" (Linux/Mac) 
or "invoke.bat" (Windows) to start the script.

Have fun!
'''
)

#---------------------------------------------
def yes_or_no(prompt:str, default_yes=True):
    default = "y" if default_yes else 'n'
    response = input(f'{prompt} [{default}] ') or default
    if default_yes:
        return response[0] not in ('n','N')
    else:
        return response[0] in ('y','Y')

#---------------------------------------------
def user_wants_to_download_weights()->str:
    '''
    Returns one of "skip", "recommended" or "customized"
    '''
    print('''You can download and configure the weights files manually or let this
script do it for you. Manual installation is described at:

https://github.com/invoke-ai/InvokeAI/blob/main/docs/installation/INSTALLING_MODELS.md

You may download the recommended models (about 10GB total), select a customized set, or
completely skip this step.
'''
    )
    selection = None
    while selection is None:
        choice = input('Download <r>ecommended models, <c>ustomize the list, or <s>kip this step? [r]: ')
        if choice.startswith(('r','R')) or len(choice)==0:
            selection = 'recommended'
        elif choice.startswith(('c','C')):
            selection = 'customized'
        elif choice.startswith(('s','S')):
            selection = 'skip'
    return selection

#---------------------------------------------
def select_datasets(action:str):
    done = False
    while not done:
        datasets = dict()
        dflt = None   # the first model selected will be the default; TODO let user change
        counter = 1

        if action == 'customized':
            print('''
Choose the weight file(s) you wish to download. Before downloading you 
will be given the option to view and change your selections.
'''
        )
            for ds in Datasets.keys():
                recommended = '(recommended)' if Datasets[ds]['recommended'] else ''
                print(f'[{counter}] {ds}:\n    {Datasets[ds]["description"]} {recommended}')
                if yes_or_no('    Download?',default_yes=Datasets[ds]['recommended']):
                    datasets[ds]=counter
                    counter += 1
        else:
            for ds in Datasets.keys():
                if Datasets[ds]['recommended']:
                    datasets[ds]=counter
                    counter += 1
                
        print('The following weight files will be downloaded:')
        for ds in datasets:
            dflt = '*' if dflt is None else ''
            print(f'   [{datasets[ds]}] {ds}{dflt}')
        print("*default")
        ok_to_download = yes_or_no('Ok to download?')
        if not ok_to_download:
            if yes_or_no('Change your selection?'):
                action = 'customized'
                pass
            else:
                done = True
        else:
            done = True
    return datasets if ok_to_download else None

#---------------------------------------------
def recommended_datasets()->dict:
    datasets = dict()
    for ds in Datasets.keys():
        if Datasets[ds]['recommended']:
            datasets[ds]=True
    return datasets
    
#-------------------------------Authenticate against Hugging Face
def authenticate():
    print('''
To download the Stable Diffusion weight files from the official Hugging Face 
repository, you need to read and accept the CreativeML Responsible AI license.

This involves a few easy steps.

1. If you have not already done so, create an account on Hugging Face's web site
   using the "Sign Up" button:

   https://huggingface.co/join

   You will need to verify your email address as part of the HuggingFace
   registration process.

2. Log into your Hugging Face account:

    https://huggingface.co/login

3. Accept the license terms located here:

   https://huggingface.co/runwayml/stable-diffusion-v1-5

   and here:

   https://huggingface.co/runwayml/stable-diffusion-inpainting

    (Yes, you have to accept two slightly different license agreements)
'''
    )
    input('Press <enter> when you are ready to continue:')
    print('(Fetching Hugging Face token from cache...',end='')
    access_token = HfFolder.get_token()
    if access_token is not None:
        print('found')
    
    if access_token is None:
        print('not found')
        print('''
4. Thank you! The last step is to enter your HuggingFace access token so that
   this script is authorized to initiate the download. Go to the access tokens
   page of your Hugging Face account and create a token by clicking the 
   "New token" button:

   https://huggingface.co/settings/tokens

   (You can enter anything you like in the token creation field marked "Name". 
   "Role" should be "read").

   Now copy the token to your clipboard and paste it here: '''
        )
        access_token = getpass_asterisk.getpass_asterisk()
    return access_token

#---------------------------------------------
# look for legacy model.ckpt in models directory and offer to
# normalize its name
def migrate_models_ckpt():
    model_path = os.path.join(Globals.root,Model_dir,Weights_dir)
    if not os.path.exists(os.path.join(model_path,'model.ckpt')):
        return
    new_name = Datasets['stable-diffusion-1.4']['file']
    print('You seem to have the Stable Diffusion v4.1 "model.ckpt" already installed.')
    rename = yes_or_no(f'Ok to rename it to "{new_name}" for future reference?')
    if rename:
        print(f'model.ckpt => {new_name}')
        os.rename(os.path.join(model_path,'model.ckpt'),os.path.join(model_path,new_name))
            
#---------------------------------------------
def download_weight_datasets(models:dict, access_token:str):
    migrate_models_ckpt()
    successful = dict()
    for mod in models.keys():
        repo_id = Datasets[mod]['repo_id']
        filename = Datasets[mod]['file']
        print(os.path.join(Globals.root,Model_dir,Weights_dir))
        success = download_with_resume(
            repo_id=repo_id,
            model_dir=os.path.join(Globals.root,Model_dir,Weights_dir),
            model_name=filename,
            access_token=access_token
        )
        if success:
            successful[mod] = True
    if len(successful) < len(models):
        print(f'\n\n** There were errors downloading one or more files. **')
        print('Please double-check your license agreements, and your access token.')
        HfFolder.delete_token()
        print('Press any key to try again. Type ^C to quit.\n')
        input()
        return None

    HfFolder.save_token(access_token)
    keys = ', '.join(successful.keys())
    print(f'Successfully installed {keys}') 
    return successful
    
#---------------------------------------------
def download_with_resume(repo_id:str, model_dir:str, model_name:str, access_token:str=None)->bool:
    model_dest = os.path.join(model_dir, model_name)
    os.makedirs(model_dir, exist_ok=True)

    url = hf_hub_url(repo_id, model_name)

    header = {"Authorization": f'Bearer {access_token}'} if access_token else {}
    open_mode = 'wb'
    exist_size = 0
    
    if os.path.exists(model_dest):
        exist_size = os.path.getsize(model_dest)
        header['Range'] = f'bytes={exist_size}-'
        open_mode = 'ab'

    resp = requests.get(url, headers=header, stream=True)
    total = int(resp.headers.get('content-length', 0))
    
    if resp.status_code==416:  # "range not satisfiable", which means nothing to return
        print(f'* {model_name}: complete file found. Skipping.')
        return True
    elif resp.status_code != 200:
        print(f'** An error occurred during downloading {model_name}: {resp.reason}')
    elif exist_size > 0:
        print(f'* {model_name}: partial file found. Resuming...')
    else:
        print(f'* {model_name}: Downloading...')

    try:
        if total < 2000:
            print(f'*** ERROR DOWNLOADING {model_name}: {resp.text}')
            return False

        with open(model_dest, open_mode) as file, tqdm(
                desc=model_name,
                initial=exist_size,
                total=total+exist_size,
                unit='iB',
                unit_scale=True,
                unit_divisor=1000,
        ) as bar:
            for data in resp.iter_content(chunk_size=1024):
                size = file.write(data)
                bar.update(size)
    except Exception as e:
        print(f'An error occurred while downloading {model_name}: {str(e)}')
        return False
    return True
                             
#---------------------------------------------
def update_config_file(successfully_downloaded:dict,opt:dict):
    config_file = opt.config_file or Default_config_file
    config_file = os.path.normpath(os.path.join(Globals.root,config_file))
    
    yaml = new_config_file_contents(successfully_downloaded,config_file)

    try:
        if os.path.exists(config_file):
            print(f'** {config_file} exists. Renaming to {config_file}.orig')
            os.rename(config_file,f'{config_file}.orig')
        tmpfile = os.path.join(os.path.dirname(config_file),'new_config.tmp')
        with open(tmpfile, 'w') as outfile:
            outfile.write(Config_preamble)
            outfile.write(yaml)
        os.rename(tmpfile,config_file)

    except Exception as e:
        print(f'**Error creating config file {config_file}: {str(e)} **')
        return

    print(f'Successfully created new configuration file {config_file}')

    
#---------------------------------------------    
def new_config_file_contents(successfully_downloaded:dict, config_file:str)->str:
    if os.path.exists(config_file):
        conf = OmegaConf.load(config_file)
    else:
        conf = OmegaConf.create()

    # find the VAE file, if there is one
    vae = None
    default_selected = False
    
    for model in successfully_downloaded:
        if Datasets[model]['config'] == 'VAE':
            vae = Datasets[model]['file']
    
    for model in successfully_downloaded:
        if Datasets[model]['config'] == 'VAE': # skip VAE entries
            continue
        stanza = conf[model] if model in conf else { }
        
        stanza['description'] = Datasets[model]['description']
        stanza['weights'] = os.path.join(Model_dir,Weights_dir,Datasets[model]['file'])
        stanza['config'] = os.path.normpath(os.path.join(SD_Configs, Datasets[model]['config']))
        stanza['width'] = Datasets[model]['width']
        stanza['height'] = Datasets[model]['height']
        stanza.pop('default',None)  # this will be set later
        if vae:
            stanza['vae'] = os.path.normpath(os.path.join(Model_dir,Weights_dir,vae))
        # BUG - the first stanza is always the default. User should select.
        if not default_selected:
            stanza['default'] = True
            default_selected = True
        conf[model] = stanza
    return OmegaConf.to_yaml(conf)
    
#---------------------------------------------
# this will preload the Bert tokenizer fles
def download_bert():
    print('Installing bert tokenizer (ignore deprecation errors)...', end='',file=sys.stderr)
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=DeprecationWarning)
        from transformers import BertTokenizerFast, AutoFeatureExtractor
        download_from_hf(BertTokenizerFast,'bert-base-uncased')
        print('...success',file=sys.stderr)

#---------------------------------------------
def download_from_hf(model_class:object, model_name:str):
    return model_class.from_pretrained(model_name,
                                       cache_dir=os.path.join(Globals.root,Model_dir,model_name),
                                       resume_download=True
    )

#---------------------------------------------
def download_clip():
    print('Installing CLIP model (ignore deprecation errors)...',end='',file=sys.stderr)
    version = 'openai/clip-vit-large-patch14'
    download_from_hf(CLIPTokenizer,version)
    download_from_hf(CLIPTextModel,version)
    print('...success',file=sys.stderr)

#---------------------------------------------
def download_realesrgan():
    print('Installing models from RealESRGAN and facexlib  (ignore deprecation errors)...',end='',file=sys.stderr)
    try:
        from realesrgan import RealESRGANer
        from realesrgan.archs.srvgg_arch import SRVGGNetCompact
        from facexlib.utils.face_restoration_helper import FaceRestoreHelper

        RealESRGANer(
            scale=4,
            model_path='https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth',
            model = SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=32, upscale=4, act_type='prelu')
        )

        FaceRestoreHelper(1, det_model='retinaface_resnet50')
        print('...success',file=sys.stderr)
    except Exception:
        print('Error loading ESRGAN:')
        print(traceback.format_exc())

def download_gfpgan():
    print('Installing GFPGAN models...',end='',file=sys.stderr)
    for model in (
            [
                'https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth',
                './models/gfpgan/GFPGANv1.4.pth'
            ],
            [
                'https://github.com/xinntao/facexlib/releases/download/v0.1.0/detection_Resnet50_Final.pth',
                './models/gfpgan/weights/detection_Resnet50_Final.pth'
            ],
            [
                'https://github.com/xinntao/facexlib/releases/download/v0.2.2/parsing_parsenet.pth',
                './models/gfpgan/weights/parsing_parsenet.pth'
            ],
    ):
        model_url,model_dest  = model[0],os.path.join(Globals.root,model[1])
        try:
            if not os.path.exists(model_dest):
                print(f'Downloading gfpgan model file {model_url}...',end='')
                os.makedirs(os.path.dirname(model_dest), exist_ok=True)
                request.urlretrieve(model_url,model_dest,ProgressBar(os.path.basename(model_dest)))
                print('...success')
        except Exception:
            print('Error loading GFPGAN:')
            print(traceback.format_exc())
    print('...success',file=sys.stderr)

#---------------------------------------------
def download_codeformer():
    print('Installing CodeFormer model file...',end='',file=sys.stderr)
    try:
        model_url  = 'https://github.com/sczhou/CodeFormer/releases/download/v0.1.0/codeformer.pth'
        model_dest = os.path.join(Globals.root,'models/codeformer/codeformer.pth')
        if not os.path.exists(model_dest):
            print('Downloading codeformer model file...')
            os.makedirs(os.path.dirname(model_dest), exist_ok=True)
            request.urlretrieve(model_url,model_dest,ProgressBar(os.path.basename(model_dest)))
    except Exception:
        print('Error loading CodeFormer:')
        print(traceback.format_exc())
    print('...success',file=sys.stderr)
    
#---------------------------------------------
def download_clipseg():
    print('Installing clipseg model for text-based masking...',end='')
    import zipfile
    try:
        model_url = 'https://owncloud.gwdg.de/index.php/s/ioHbRzFx6th32hn/download'
        model_dest = os.path.join(Globals.root,'models/clipseg/clipseg_weights')
        weights_zip = 'models/clipseg/weights.zip'
        
        if not os.path.exists(model_dest):
            os.makedirs(os.path.dirname(model_dest), exist_ok=True)
        if not os.path.exists(f'{model_dest}/rd64-uni-refined.pth'):
            dest = os.path.join(Globals.root,weights_zip)
            request.urlretrieve(model_url,dest)
            with zipfile.ZipFile(dest,'r') as zip:
                zip.extractall(os.path.join(Globals.root,'models/clipseg'))
            os.remove(dest)

            from clipseg.clipseg import CLIPDensePredT
            model = CLIPDensePredT(version='ViT-B/16', reduce_dim=64, )
            model.eval()
            model.load_state_dict(
                torch.load(
                    os.path.join(Globals.root,'models/clipseg/clipseg_weights/rd64-uni-refined.pth'),
                    map_location=torch.device('cpu')
                    ),
                strict=False,
            )
    except Exception:
        print('Error installing clipseg model:')
        print(traceback.format_exc())
    print('...success')

#-------------------------------------
def download_safety_checker():
    print('Installing safety model for NSFW content detection...',end='',file=sys.stderr)
    try:
        from diffusers.pipelines.stable_diffusion.safety_checker import StableDiffusionSafetyChecker
        from transformers import AutoFeatureExtractor
    except ModuleNotFoundError:
        print('Error installing safety checker model:')
        print(traceback.format_exc())
        return
    safety_model_id = "CompVis/stable-diffusion-safety-checker"
    download_from_hf(AutoFeatureExtractor,safety_model_id)
    download_from_hf(StableDiffusionSafetyChecker,safety_model_id)
    print('...success',file=sys.stderr)

#-------------------------------------
def download_weights(opt:dict):
    if opt.yes_to_all:
        models = recommended_datasets()
        access_token = HfFolder.get_token()
        if len(models)>0 and access_token is not None:
            successfully_downloaded = download_weight_datasets(models, access_token)
            update_config_file(successfully_downloaded,opt)
            return
        else:
            print('** Cannot download models because no Hugging Face access token could be found. Please re-run without --yes')
    else:
        choice = user_wants_to_download_weights()

    if choice == 'recommended':
        models = recommended_datasets()
    elif choice == 'customized':
        models = select_datasets(choice)
        if models is None and yes_or_no('Quit?',default_yes=False):
                sys.exit(0)
    else:  # 'skip'
        return

    print('** LICENSE AGREEMENT FOR WEIGHT FILES **')
    access_token = authenticate()
    print('\n** DOWNLOADING WEIGHTS **')
    successfully_downloaded = download_weight_datasets(models, access_token)
    update_config_file(successfully_downloaded,opt)

#-------------------------------------
def get_root(root:str=None)->str:
    if root:
        return root
    elif os.environ.get('INVOKEAI_ROOT'):
        return os.environ.get('INVOKEAI_ROOT')
    else:
        init_file = os.path.expanduser(Globals.initfile)
        if not os.path.exists(init_file):
            return '.'

    # if we get here, then we read from initfile
    root = None
    with open(init_file, 'r') as infile:
        lines = infile.readlines()
        for l in lines:
            match = re.search('--root\s*=?\s*"?([^"]+)"?',l)
            if match:
                root = match.groups()[0]

    return root.strip() or '.'

#-------------------------------------
def initialize_rootdir(root:str):
    assert os.path.exists('./configs'),'Run this script from within the top level of the InvokeAI source code directory, "InvokeAI"'
    print(f'** INITIALIZING INVOKEAI ROOT DIRECTORY **')
    print(f'Creating a directory named {root} to contain InvokeAI models, configuration files and outputs.')
    print(f'If you move this directory, please change its location using the --root option in "{Globals.initfile},')
    print(f'or set the environment variable INVOKEAI_ROOT to the new location.\n')
    for name in ('models','configs','outputs','scripts'):
        os.makedirs(os.path.join(root,name), exist_ok=True)
    for src in ('configs','scripts'):
        dest = os.path.join(root,src)
        if not os.path.samefile(src,dest):
            shutil.copytree(src,dest,dirs_exist_ok=True)

    init_file = os.path.expanduser(Globals.initfile)
    if not os.path.exists(init_file):
        print(f'Creating a basic initialization file at "{init_file}".\n')
        with open(init_file,'w') as f:
            f.write(f'''# InvokeAI initialization file
# The --root option below points to the folder in which InvokeAI stores its models, configs and outputs.
# Don't change it unless you know what you are doing!
--root="{Globals.root}"

# You may place other  frequently-used startup commands here, one or more per line.
# Examples:
# --web --host=0.0.0.0
# --steps=20
# -Ak_euler_a -C10.0
#
'''
            )
    
#-------------------------------------
class ProgressBar():
    def __init__(self,model_name='file'):
        self.pbar = None
        self.name = model_name

    def __call__(self, block_num, block_size, total_size):
        if not self.pbar:
            self.pbar=tqdm(desc=self.name,
                           initial=0,
                           unit='iB',
                           unit_scale=True,
                           unit_divisor=1000,
                           total=total_size)
        self.pbar.update(block_size)

#-------------------------------------
def main():
    parser = argparse.ArgumentParser(description='InvokeAI model downloader')
    parser.add_argument('--interactive',
                        dest='interactive',
                        action=argparse.BooleanOptionalAction,
                        default=True,
                        help='run in interactive mode (default)')
    parser.add_argument('--yes','-y',
                        dest='yes_to_all',
                        action='store_true',
                        help='answer "yes" to all prompts')
    parser.add_argument('--config_file',
                        '-c',
                        dest='config_file',
                        type=str,
                        default='./configs/models.yaml',
                        help='path to configuration file to create')
    parser.add_argument('--root',
                        dest='root',
                        type=str,
                        default=None,
                        help='path to root of install directory')
    opt = parser.parse_args()

    # setting a global here
    Globals.root = os.path.expanduser(get_root(opt.root))

    try:
        introduction()

        # We check for this specific file, without which we are toast...
        if not os.path.exists(os.path.join(Globals.root,'configs/stable-diffusion/v1-inference.yaml')):
            initialize_rootdir(Globals.root)

        if opt.interactive:
            print('** DOWNLOADING DIFFUSION WEIGHTS **')
            download_weights(opt)
        print('\n** DOWNLOADING SUPPORT MODELS **')
        download_bert()
        download_clip()
        download_realesrgan()
        download_gfpgan()
        download_codeformer()
        download_clipseg()
        download_safety_checker()
        postscript()
    except KeyboardInterrupt:
        print('\nGoodbye! Come back soon.')
    except Exception as e:
        print(f'\nA problem occurred during download.\nThe error was: "{str(e)}"')
    
#-------------------------------------
if __name__ == '__main__':
    main()

    