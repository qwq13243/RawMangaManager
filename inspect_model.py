import torch
import os
import sys

# Add core path
sys.path.append(os.path.join(os.getcwd(), 'core'))

from core.saber.lama.model import LamaFourier
from core.saber.utils import resource_path

def inspect():
    device = torch.device('cpu')
    model_path = resource_path("models/lama/inpainting_lama_mpe.ckpt")
    
    print(f"Checking model at: {model_path}")
    if not os.path.exists(model_path):
        print("Model file not found!")
        return

    try:
        checkpoint = torch.load(model_path, map_location='cpu')
        if 'state_dict' in checkpoint:
            state_dict = checkpoint['state_dict']
        else:
            state_dict = checkpoint
            
        print(f"Checkpoint keys (first 5): {list(state_dict.keys())[:5]}")
        
        # Check if 'gen_state_dict' exists (ZITS style?)
        if 'gen_state_dict' in state_dict:
             print("Found 'gen_state_dict' key! Trying to use it.")
             state_dict = state_dict['gen_state_dict']
             print(f"New state dict keys (first 5): {list(state_dict.keys())[:5]}")
        
        model = LamaFourier(use_mpe=False)
        model_keys = list(model.state_dict().keys())
        print(f"Model keys (first 5): {model_keys[:5]}")
        
        # Check intersection
        ckpt_keys = set(state_dict.keys())
        mdl_keys = set(model_keys)
        
        intersection = ckpt_keys.intersection(mdl_keys)
        print(f"Matching keys: {len(intersection)} / {len(mdl_keys)}")
        
        if len(intersection) == 0:
            print("No keys match! Trying to fix prefix...")
            # Try removing 'generator.'
            fixed_keys = {k.replace('generator.', ''): v for k, v in state_dict.items()}
            intersection_fixed = set(fixed_keys.keys()).intersection(mdl_keys)
            print(f"Matching keys after removing 'generator.': {len(intersection_fixed)}")
            
            # Try adding 'generator.'
            fixed_keys_2 = {'generator.' + k: v for k, v in state_dict.items()}
            intersection_fixed_2 = set(fixed_keys_2.keys()).intersection(mdl_keys)
            print(f"Matching keys after adding 'generator.': {len(intersection_fixed_2)}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect()