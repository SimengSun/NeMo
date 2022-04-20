import json
import os
import shutil
from typing import Tuple
import time

import torch
from pyannote.core import Annotation, Segment
from pyannote.metrics import detection

from nemo.collections.asr.metrics.wer import word_error_rate
from nemo.collections.asr.parts.utils.vad_utils import contruct_manfiest_eval, plot, stitch_segmented_asr_output, write_ss2manifest

from extract_speech import process_one_file
import multiprocessing
import tqdm
import numpy as np

def evaluate_vad(manifest: str, ref: str = "oracle_vad") -> Tuple[float, float, float]:
    metric = detection.DetectionErrorRate()
    
    for line in open(manifest, 'r', encoding='utf-8'):
        sample = json.loads(line)

        reference = Annotation()
        vad_ref = torch.load(sample[ref])
        for row in vad_ref:
            reference[Segment(row[0], row[1])] = 'Speech'
        
        hypothesis = Annotation()
        if sample['speech_segments_filepath'] != "":
            if sample['speech_segments_filepath'].endswith(".pt"):
                vad_hyp = torch.load(sample['speech_segments_filepath'])
            else:
                vad_hyp = np.load(sample['speech_segments_filepath'])
            for row in vad_hyp:
                hypothesis[Segment(row[0], row[1])] = 'Speech'

        metric(reference, hypothesis) 

    # vad evaluation
    report = metric.report(display=False)
    DetER = report.iloc[[-1]][('detection error rate', '%')].item()
    FA = report.iloc[[-1]][('false alarm', '%')].item()
    MISS = report.iloc[[-1]][('miss', '%')].item()
    return DetER, FA, MISS

def evaluate_asr(manifest: str, use_cer: bool=False, no_space=False) -> Tuple[float,float]:
    predicted_text, ground_truth_text = [], []
    predicted_text_nospace, ground_truth_text_nospace = [], []

    for line in open(manifest, 'r', encoding='utf-8'):
        sample = json.loads(line)
        predicted_text.append(sample['pred_text'])
        predicted_text_nospace.append(sample['pred_text'].replace(" ", ""))
        ground_truth_text.append(sample['text'])
        ground_truth_text_nospace.append(sample['text'].replace(" ", ""))

    # asr evaluation
    WER_nospace = 1.0
    WER = word_error_rate(hypotheses=predicted_text, references=ground_truth_text, use_cer=use_cer)
    if no_space:
        WER_nospace = word_error_rate(hypotheses=predicted_text_nospace, references=ground_truth_text_nospace, use_cer=use_cer)
    return WER, WER_nospace

def perform_energy_vad(input_manifest, output_manifest="generated_energy_ss_manifest.json"):
    data = []
    for line in open(input_manifest, 'r', encoding='utf-8'):
        data.append(json.loads(line))
       
    number_of_processes = 15
    p = multiprocessing.Pool(number_of_processes)
    results = []
    for result in tqdm.tqdm(p.imap_unordered(process_one_file, data), total=len(data)):
        results.append(result)
        
    p.close()
    
    with open(output_manifest, "w") as fout:
        for result in results:
            # each file might have multi meta
            for meta in result:
                json.dump(meta, fout)
                fout.write('\n')
                fout.flush()
            
    return output_manifest


def switch_lang_model(lang: str, model: str) -> Tuple[bool, bool, bool, str]:

    lang_model_table = {
        'english-nr_citrinet':  (False, False, True, "/home/fjia/models/english/Citrinet_Aug_1024_Gamma_0-25_NeMo_ASRSET_2.0_e200.nemo"),
        'english-nr_conformer_transducer':  (False, False, True, "/home/fjia/models/jagadeesh_nr/english/aug/rno8_bucket32_Aug_nemo2.0_d512_adamwlr5.0_wd1e-3_aug10x0.05spunigram1024_emit0_nodes32_bucketing_200e_b4.nemo"),
        'mandarin-citrinet': (True, True, False, "stt_zh_citrinet_1024_gamma_0_25"), # test 5000
        'french-citrinet':   (False, False, False, "stt_fr_citrinet_1024_gamma_0_25"), # test 2320
        'german-citrinet':   (False, False, False, "stt_de_citrinet_1024"), #dev 15845 test 15854
        'spanish-nr_citrinet':  (False, False, True, "/home/fjia/models/jagadeesh_nr/spanish/finetuning_with_augmentation/stt_es_citrinet_1024_gamma_0_25.nemo"), 
        'spanish-nr_conformer_ctc':  (False, False, True, "/home/fjia/models/jagadeesh_nr/spanish/finetuning_with_augmentation/stt_es_conformer_ctc_large.nemo"), 
        'spanish-nr_conformer_transducer':  (False, False, True, "/home/fjia/models/jagadeesh_nr/spanish/finetuning_with_augmentation/stt_es_conformer_transducer_large.nemo"), 
        'spanish-nr_contextnet':  (False, False, True, "/home/fjia/models/jagadeesh_nr/spanish/finetuning_with_augmentation/stt_es_contextnet_1024.nemo"), 
        'russian-citrinet':  (False, False, False, "/home/fjia/models/vitaly/ru_model/CitriNet-1024-8x-Stride-Gamma-0.25.nemo"), #dev 9361 test 9355 TODO will ask vitaly about new checkpoint  
    }
    
    lang_model = lang + "-" + model
    return lang_model_table.get(lang_model, None)


def switch_model_buffered_param(model: str) -> float:
     # TODO other models
    model_streaming_param_table = {
        'citrinet': 8 ,
        'conformer_ctc': 4
    }
    if "nr" in model:
        model = model.split("nr_")[1]

    return model_streaming_param_table.get(model, None)

def main():
    """
    modes = ['streaming', 'offline]
    langs = ['english', 'mandarin', 'french', 'german',  'spanish', 'russian']
    vad_exps = ["novad", "oracle_vad", "neural_vad", "energy_vad"] 
    models = ['citrinet', 'nr_citrinet', 'nr_conformer_ctc', 'nr_conformer_transducer', 'nr_contextnet'] # no russian citrinet now
    db_list = [0,5,10,15,20,'clean']
    """

    db_list = [0,5,10,15,20,'clean']
    modes = ['streaming', 'offline']
    langs = ['english', 'spanish', 'mandarin', 'french', 'german', 'russian']
    vad_exps = ["novad", "oracle_vad", "neural_vad", "energy_vad"] 
    models = ['nr_citrinet', 'nr_conformer_ctc', 'citrinet']

    subset="test"
    single= True
    exp = "_single" if single else ""
    res_file = f"res{exp}_asr_streaming_offline.csv"

    final_output_folder = "final"
    save_neural_vad = True
    os.makedirs(final_output_folder, exist_ok=True)

    for mode in modes:
        mode_folder = f'{final_output_folder}/{mode}'
        os.makedirs(mode_folder, exist_ok=True)

        for lang in langs:
            for model in models:
                if mode == "streaming":
                    if switch_model_buffered_param(model):
                        model_stride = switch_model_buffered_param(model)
                    else:
                        print(f"Currently do not support {mode} in streaming/buffered model")
                        continue

                if switch_lang_model(lang, model):
                    use_cer, no_space, use_model_path, asr_model = switch_lang_model(lang, model)
                else:
                    print(f"{lang} with {model} does not exist")
                    continue

                for vad_exp in vad_exps:
                    for db in db_list:
                        start = time.time()
                        mode_lang_folder = f"{final_output_folder}/{mode}/{lang}"
                        if os.path.exists(mode_lang_folder):
                            shutil.rmtree(mode_lang_folder)
                        os.makedirs(mode_lang_folder, exist_ok=True)

                        if db=='clean':
                            input_manifest=f"/home/fjia/code/5_syn/{lang}_{subset}{exp}.json"
                        else:
                            input_manifest = f"/data/syn_noise_augmented/manifests/{lang}_{subset}{exp}_test_noise_0_30_musan_fs_{db}db.json"

                        if mode == "offline":
                            if vad_exp =="novad":
                                novad_output_manifest= f"{final_output_folder}/{mode}/{lang}/asr_{vad_exp}_{model}_output_manifest_{db}.json"
                                if use_model_path:
                                    os.system(f'python ../transcribe_speech.py \
                                        model_path={asr_model} \
                                        dataset_manifest={input_manifest} \
                                        batch_size=32 \
                                        amp=True \
                                        output_filename={novad_output_manifest}') 
                                    WER, WER_nospace = evaluate_asr(novad_output_manifest, use_cer=use_cer, no_space=no_space)
                                    print(f"no vad WER is {WER}, no vad WER no_space is {WER_nospace}")

                                else:
                                    os.system(f'python ../transcribe_speech.py \
                                        pretrained_name={asr_model} \
                                        dataset_manifest={input_manifest} \
                                        batch_size=32 \
                                        amp=True \
                                        output_filename={novad_output_manifest}') 
                                    WER, WER_nospace = evaluate_asr(novad_output_manifest, use_cer=use_cer, no_space=no_space)
                                    print(f"no vad WER is {WER}, no vad WER no_space is {WER_nospace}")

                            elif vad_exp in ["neural_vad", "energy_vad", "oracle_vad"]:
                                vad_out_manifest_filepath= os.path.join(mode_lang_folder, f"vad_out_{vad_exp}.json")

                                if vad_exp=="neural_vad":
                                    params = {
                                        "onset": 0.5,
                                        "offset": 0.5,
                                        "min_duration_on": 0.5,
                                        "min_duration_off": 0.5,
                                        "pad_onset": 0.2,
                                        "pad_offset": -0.2
                                    }
                                    # vad_model="/home/fjia/models/mVAD_lin_nonoise_marblenet-3x2x64-4N-256bs-50e-0.02lr-0.001wd/slurm_mVAD_lin_nonoise_marblenet-3x2x64-4N-256bs-50e-0.02lr-0.001wd/checkpoints/mVAD_lin_nonoise_marblenet-3x2x64-4N-256bs-50e-0.02lr-0.001wd.nemo" # here we use vad_marblenet for example, you can choose other VAD models.
                                    vad_model="/home/fjia/models/mVAD_lin_marblenet-3x2x64-4N-256bs-50e-0.02lr-0.001wd/slurm_mVAD_lin_marblenet-3x2x64-4N-256bs-50e-0.02lr-0.001wd/checkpoints/mVAD_lin_marblenet-3x2x64-4N-256bs-50e-0.02lr-0.001wd.nemo" # here we use vad_marblenet for example, you can choose other VAD models.
                                    
                                    if save_neural_vad:
                                        frame_out_dir = f"{final_output_folder}/{mode}/{lang}/{model}/neural_vad_{db}"
                                    else:
                                        frame_out_dir = os.path.join(mode_lang_folder, "neural_vad")
                                    os.system(f'python vad_infer.py --config-path="../conf/VAD" --config-name="vad_inference_postprocessing.yaml" \
                                        dataset={input_manifest} \
                                        vad.model_path={vad_model} \
                                        frame_out_dir={frame_out_dir} \
                                        vad.parameters.window_length_in_sec=0.63 \
                                        vad.parameters.postprocessing.onset={params["onset"]} \
                                        vad.parameters.postprocessing.offset={params["offset"]} \
                                        vad.parameters.postprocessing.min_duration_on={params["min_duration_on"]} \
                                        vad.parameters.postprocessing.min_duration_off={params["min_duration_off"]} \
                                        vad.parameters.postprocessing.pad_onset={params["pad_onset"]} \
                                        vad.parameters.postprocessing.pad_offset={params["pad_offset"]} \
                                        out_manifest_filepath={vad_out_manifest_filepath}')  

                                elif vad_exp=="energy_vad":
                                    vad_out_manifest_filepath = perform_energy_vad(input_manifest, vad_out_manifest_filepath)

                                else: # oracle_vad and energy_oracle_vad
                                    vad_out_manifest_filepath = write_ss2manifest(input_manifest, vad_exp, vad_out_manifest_filepath)

                                segmented_output_manifest = os.path.join(mode_lang_folder, "asr_segmented_output_manifest.json")

                                if use_model_path:
                                    os.system(f'python ../transcribe_speech.py \
                                        model_path={asr_model} \
                                        dataset_manifest={vad_out_manifest_filepath} \
                                        batch_size=32 \
                                        amp=True \
                                        output_filename={segmented_output_manifest}')
                                else:
                                    os.system(f'python ../transcribe_speech.py \
                                        pretrained_name={asr_model} \
                                        dataset_manifest={vad_out_manifest_filepath} \
                                        batch_size=32 \
                                        amp=True \
                                        output_filename={segmented_output_manifest}')

                                stitched_output_manifest = os.path.join(mode_lang_folder, "/stitched_asr_output_manifest.json")
                                stitched_output_manifest = stitch_segmented_asr_output(
                                    segmented_output_manifest,
                                    speech_segments_tensor_dir = os.path.join(mode_lang_folder, "speech_segments"),
                                    stitched_output_manifest = stitched_output_manifest)

                                aligned_vad_asr_output_manifest = f"{final_output_folder}/{mode}/{lang}/asr_{vad_exp}_{model}_output_manifest_{db}.json"
                                aligned_vad_asr_output_manifest = contruct_manfiest_eval(input_manifest, stitched_output_manifest, aligned_vad_asr_output_manifest)

                                DetER, FA, MISS = evaluate_vad(aligned_vad_asr_output_manifest)
                                print(f'DetER (%) : {DetER}, FA (%): {FA}, MISS (%): {MISS}')

                                WER, WER_nospace = evaluate_asr(aligned_vad_asr_output_manifest, use_cer=use_cer, no_space=no_space)
                                print(f"vad WER is {WER}, vad WER no_space is {WER_nospace}")

                            else:
                                raise ValueError(f"vad_exp could only be in novad, energy_vad, neural_vad and oracle_vad but got {vad_exp}")


                        elif mode == "streaming":
                            chunk_len_in_ms = 160 
                            if vad_exp =="novad":
                                novad_output_manifest= f"{final_output_folder}/{mode}/{lang}/asr_{vad_exp}_{model}_output_manifest_{db}.json"
                               
                                os.system(f'python ../asr_chunked_inference/ctc/speech_to_text_buffered_infer_ctc.py \
                                --asr_model {asr_model} \
                                --test_manifest {input_manifest} \
                                --chunk_len_in_ms {chunk_len_in_ms} \
                                --output_path {novad_output_manifest} \
                                --batch_size 128 \
                                --model_stride {model_stride} \
                                --total_buffer_in_secs 4')

                                WER, WER_nospace = evaluate_asr(novad_output_manifest, use_cer=use_cer, no_space=no_space)
                                print(f"no vad WER is {WER}, no vad WER no_space is {WER_nospace}")

                            elif vad_exp in ["neural_vad", "energy_vad", "oracle_vad"]:
                                vad_out_manifest_filepath= os.path.join(mode_lang_folder, f"vad_out_{vad_exp}.json")
                                vad_asr_output_manifest= f"{final_output_folder}/{mode}/{lang}/asr_{vad_exp}_{model}_output_manifest_{db}.json"
                                aligned_vad_asr_output_manifest = f"{final_output_folder}/{mode}/{lang}/asr_{vad_exp}_{model}_output_manifest_{db}.json"

                                if vad_exp=="neural_vad":
                                    # vad_model="/home/fjia/models/mVAD_lin_nonoise_marblenet-3x2x64-4N-256bs-50e-0.02lr-0.001wd/slurm_mVAD_lin_nonoise_marblenet-3x2x64-4N-256bs-50e-0.02lr-0.001wd/checkpoints/mVAD_lin_nonoise_marblenet-3x2x64-4N-256bs-50e-0.02lr-0.001wd.nemo" # here we use vad_marblenet for example, you can choose other VAD models.
                                    vad_model="/home/fjia/models/mVAD_lin_marblenet-3x2x64-4N-256bs-50e-0.02lr-0.001wd/slurm_mVAD_lin_marblenet-3x2x64-4N-256bs-50e-0.02lr-0.001wd/checkpoints/mVAD_lin_marblenet-3x2x64-4N-256bs-50e-0.02lr-0.001wd.nemo" # here we use vad_marblenet for example, you can choose other VAD models.
                                    threshold = 0.5 # same as onset offset
                                    look_back = 4
                                    if save_neural_vad:
                                        frame_out_dir = f"{final_output_folder}/{mode}/{lang}/{model}/neural_vad_{db}"
                                    else:
                                        frame_out_dir = os.path.join(mode_lang_folder, "neural_vad")

                                    os.system(f'python ../asr_chunked_inference/ctc/speech_to_text_buffered_infer_ctc.py \
                                    --asr_model {asr_model} \
                                    --vad_model {vad_model} \
                                    --test_manifest {input_manifest} \
                                    --chunk_len_in_ms {chunk_len_in_ms} \
                                    --output_path {vad_asr_output_manifest} \
                                    --batch_size 128 \
                                    --model_stride {model_stride} \
                                    --total_buffer_in_secs 4 \
                                    --threshold {threshold} \
                                    --look_back {look_back} \
                                    --vad_before_asr')

                                    aligned_vad_asr_output_manifest = contruct_manfiest_eval(input_manifest, vad_asr_output_manifest, aligned_vad_asr_output_manifest)
                                
                                elif vad_exp=="energy_vad":
                                    # no look back
                                    vad_out_manifest_filepath = perform_energy_vad(input_manifest, vad_out_manifest_filepath)

                                    os.system(f'python ../asr_chunked_inference/ctc/speech_to_text_buffered_infer_ctc.py \
                                    --asr_model {asr_model} \
                                    --test_manifest {vad_out_manifest_filepath} \
                                    --chunk_len_in_ms {chunk_len_in_ms} \
                                    --output_path {vad_asr_output_manifest} \
                                    --batch_size 128 \
                                    --model_stride {model_stride} \
                                    --total_buffer_in_secs 4')

                                    stitched_output_manifest = os.path.join(mode_lang_folder, "stitched_asr_output_manifest.json")
                                    stitched_output_manifest = stitch_segmented_asr_output(
                                        vad_asr_output_manifest,
                                        speech_segments_tensor_dir = os.path.join(mode_lang_folder, "speech_segments"),
                                        stitched_output_manifest = stitched_output_manifest)
                                    aligned_vad_asr_output_manifest = contruct_manfiest_eval(input_manifest, stitched_output_manifest, aligned_vad_asr_output_manifest)
                                

                                else: # oracle_vad and energy_oracle_vad
                                    vad_out_manifest_filepath = write_ss2manifest(input_manifest, vad_exp, vad_out_manifest_filepath)

                                    os.system(f'python ../asr_chunked_inference/ctc/speech_to_text_buffered_infer_ctc.py \
                                    --asr_model {asr_model} \
                                    --test_manifest {vad_out_manifest_filepath} \
                                    --chunk_len_in_ms {chunk_len_in_ms} \
                                    --output_path {vad_asr_output_manifest} \
                                    --batch_size 128 \
                                    --model_stride {model_stride} \
                                    --total_buffer_in_secs 4')

                                    stitched_output_manifest = os.path.join(mode_lang_folder, "stitched_asr_output_manifest.json")
                                    stitched_output_manifest = stitch_segmented_asr_output(
                                        vad_asr_output_manifest,
                                        speech_segments_tensor_dir = os.path.join(mode_lang_folder, "speech_segments"),
                                        stitched_output_manifest = stitched_output_manifest)
                                    aligned_vad_asr_output_manifest = contruct_manfiest_eval(input_manifest, stitched_output_manifest, aligned_vad_asr_output_manifest)
                                

                                
                                DetER, FA, MISS = evaluate_vad(aligned_vad_asr_output_manifest)
                                print(f'DetER (%) : {DetER}, FA (%): {FA}, MISS (%): {MISS}')

                                WER, WER_nospace = evaluate_asr(aligned_vad_asr_output_manifest, use_cer=use_cer, no_space=no_space)
                                print(f"vad WER is {WER}, vad WER no_space is {WER_nospace}")

                            else:
                                raise ValueError(f"vad_exp could only be in novad, energy_vad, neural_vad and oracle_vad but got {vad_exp}")

                        else:
                            raise ValueError(f"Invalid mode {mode}. Mode could be either streaming or offline.")

                        end = time.time()
                        run_time = end-start
                        # collecting evaluation result
                        with open(res_file, "a") as fp:
                            if vad_exp == "novad" or vad_exp=="oracle_vad":
                                fp.write(f"{subset},{mode},{lang},{model},{db},{vad_exp},{round(WER, 4)},{round(WER_nospace, 4)},{round(run_time, 4)}")
                                fp.write("\n")
                            elif vad_exp == "energy_vad" :
                                fp.write(f'{subset},{mode},{lang},{model},{db},{vad_exp},{round(WER, 4)},{round(WER_nospace, 4)},{DetER},{FA},{MISS},{round(run_time, 4)}')
                                fp.write("\n")
                            else:
                                if mode == 'streaming':
                                    # think about how to convert patience to min_duration_on off and look back to pad
                                    fp.write(f'{subset},{mode},{lang},{model},{db},{vad_exp},{round(WER, 4)},{round(WER_nospace, 4)},{DetER},{FA},{MISS},{round(run_time, 4)}')
                                else:
                                    fp.write(f'{subset},{mode},{lang},{model},{db},{vad_exp},{round(WER, 4)},{round(WER_nospace, 4)},{DetER},{FA},{MISS},{round(params["onset"], 4)},{round(params["offset"], 4)},{round(params["min_duration_on"], 4)},{round(params["min_duration_off"], 4)},{round(params["pad_onset"], 4)},{round(params["pad_offset"], 4)},{round(run_time, 4)}')
                                fp.write("\n")


                
if __name__ == '__main__':
    main()