import pandas as pd
import numpy as np
import scipy
import matplotlib.pyplot as plt
from math import floor, log, ceil
from scipy.interpolate import interp1d

from cell_models import protocols, kernik, paci_2018, trace
from cell_models.rtxi.rtxi_data_exploration import get_exp_as_df
from h5py import File


class TargetObjective():
    def __init__(self, time, voltage, current,
                 protocol_type, model_name=None,  capacitance=None,
                 target_protocol=None, target_meta=None,
                 times_to_compare=None, g_ishi=None):
        """
        Parameters
        ----------
            time – time in milliseconds
            voltage – voltage in mV
            current – current in A/F
            capacitance – cell capacitance in pF
            protocol_type – String describing the protocol type
            target_protocol – Protocol object if the TargetObjective is
                not experimental. Otherwise None
            is_exp – One of the following strings:
                'Experimental'
                'Kernik'
                'Paci'
            target_meta – metadata object with information about where
                the data came from
            freq – sampling frequency in 1/ms
        """
        #cell-specific stuff
        self.time = time
        self.voltage = voltage
        self.current = current

        #protocol-specific
        self.protocol_type = protocol_type
        self.target_protocol = target_protocol
        self.model_name = model_name 

        #target-specific
        self.target_meta = target_meta
        self.times_to_compare = times_to_compare
        self.g_ishi = g_ishi

        if ((self.target_protocol is None) or
                isinstance(self.target_protocol, TargetObjective)):
            self.freq = 1/(self.time[1] - self.time[0])
        else:
            self.freq = 10#samples/ms
            self.interp_t_v_c()
            
    def interp_t_v_c(self):
        max_sim_time = self.time[-1]
        t_interp = np.linspace(0, max_sim_time, max_sim_time * self.freq)

        f_curr = interp1d(self.time, self.current)
        f_voltage = interp1d(self.time, self.voltage)

        curr_interp = f_curr(t_interp)
        voltage_interp = f_voltage(t_interp)

        self.time = t_interp
        self.voltage = voltage_interp
        self.current = curr_interp

    def plot_data(self, title, saved_to=None):
        fig, axes = plt.subplots(2, 1, figsize=(10,8), sharex=True)
        axes[0].set_ylabel('Voltage (mV)', fontsize=20)
        axes[0].plot(self.time, self.voltage)
        axes[0].tick_params(labelsize=14)

        axes[1].set_ylabel('Current (nA/nF)', fontsize=20)
        axes[1].set_xlabel('Time (ms)', fontsize=20)
        axes[1].plot(self.time, self.current)
        axes[1].tick_params(labelsize=14)
        #axes[1].set_ylim([-.5, .5])
        axes[0].spines['top'].set_visible(False)
        axes[0].spines['right'].set_visible(False)
        axes[1].spines['top'].set_visible(False)
        axes[1].spines['right'].set_visible(False)

        fig.suptitle(title, fontsize=24)
        if saved_to is not None:
            plt.savefig(f'{saved_to}.eps', format='eps')
            plt.savefig(f'{saved_to}.png')

        plt.show()

    def filter_signal(self):
        fft = scipy.fft(self.target.current)

        bp=fft[:]
        for i in range(len(bp)): # (H-red)
            if ((i>=200 and i<=57000) or (i > 60000)):
                bp[i]=0
        ibp=scipy.ifft(bp)

        window = 4
        start_indices = np.array(range(0,floor(len(ibp)/window)))*window

        new_currents = []
        new_times = []
        new_voltages = []
        for start in start_indices:
            mid_index = floor(start + window/2)
            new_times.append(self.target.time[mid_index])
            new_voltages.append(self.target.voltage[mid_index]) 
            
            new_currents.append(np.mean(ibp[start:(start+window)].real))

        self.target = pd.DataFrame({'t': np.array(new_times)*1000.0,
                                    'I': np.array(new_currents)*1E12/self.cm,
                                    'V': new_voltages})

    def compare_individual(self, individual_model,
            max_iters=1, return_all_errors=False):

        individual_model.find_steady_state(max_iters=max_iters)

        #Cases:
            #1 is to see if the target is a simulation. If not, pass in
                #entire target
            #2 is to see if there is a no_ion_selective dictionary. If yes,
                #pass in self.target_protocol with no_ion_selective to False
            #3 if there is a no_ino_selective dictionary and your target is
                #a VoltageClampProtocol, then set is_no_ionselective to False
            #4 set is_no_ion_selective to True if you have the dict and the
                #protocol is not VC

        if self.target_protocol is not None:
            if individual_model.no_ion_selective is None:
                individual_tr = individual_model.generate_response(
                        self.target_protocol, is_no_ion_selective=False)
            elif isinstance(self.target_protocol, protocols.VoltageClampProtocol):
                individual_tr = individual_model.generate_response(
                        self.target_protocol, is_no_ion_selective=False)
            else:
                individual_tr = individual_model.generate_response(
                        self.target_protocol, is_no_ion_selective=True)
        else:
            individual_tr = individual_model.generate_response(
                    self, is_no_ion_selective=False)
            
        if individual_tr.default_unit == 'standard':
            scale = 1000
        elif individual_tr.default_unit == 'milli':
            scale = 1

        ind_time = individual_tr.t * scale
        ind_voltage = individual_tr.y * scale
        ind_current = individual_tr.current_response_info.get_current_summed()

        if self.protocol_type == 'Voltage Clamp':
            max_simulated_t = ind_time.max()
            max_exp_index = int(round(self.freq * max_simulated_t)) - 1

            t_interp = self.time[0:max_exp_index]
            f = interp1d(ind_time, ind_current)

            ind_interp_current = f(t_interp)

            if self.times_to_compare is not None:
                if len(self.current) == 2:
                    curr = [self.current[0][0:max_exp_index],
                            self.current[1][0:max_exp_index]]
                else:
                    curr = self.current[0:max_exp_index]
                error = self.calc_errors_in_ranges(ind_interp_current,
                        curr, return_all_errors=return_all_errors)
            else:
                if len(self.current) == 2:
                    print('Need to implement min-max cost function when no target range is specified. In target_objective.TargetObjective.compare_individual()')
                error = sum(abs(ind_interp_current - self.current[0:max_exp_index]))

        elif self.protocol_type == 'Dynamic Clamp':
            max_simulated_t = ind_time.max()

            max_exp_index = int(round(self.freq * max_simulated_t)) - 1

            t_interp = self.time[0:max_exp_index]
            f = interp1d(ind_time, ind_voltage)

            ind_interp_voltage = f(t_interp)

            error = sum(abs(ind_interp_voltage - self.voltage[0:max_exp_index]))
        else:
            raise('I have only implemented comparisons for VC and dynamic clamp data')

        return error

    def calc_errors_in_ranges(self, ind_current, target_current,
            return_all_errors=False):
        target_ranges = self.times_to_compare

        errors = []

        for current_name, t_range in target_ranges.items():
            start_idx = self.get_index_at_tame(t_range[0])
            end_idx = self.get_index_at_tame(t_range[1])

            if len(target_current) == 2:
                ind_sub_curr = ind_current[start_idx:end_idx]
                targ_min_curr = target_current[0][start_idx:end_idx]
                targ_max_curr = target_current[1][start_idx:end_idx]
                not_in_range = [i for i, v in
                        enumerate(ind_sub_curr)
                        if ((v < targ_min_curr[i]) or
                            (v > targ_max_curr[i]))]
                if not not_in_range:
                    new_error = 0
                else:
                    range_errors = []
                    for i in not_in_range:
                        if ind_sub_curr[i] < targ_min_curr[i]:
                            range_errors.append(
                                    abs(ind_sub_curr[i] - targ_min_curr[i]))
                        else:
                            range_errors.append(
                                    abs(ind_sub_curr[i] - targ_max_curr[i]))
                    new_error = sum(range_errors)
            else:
                new_error = sum(abs(ind_current[start_idx:end_idx] -
                                    target_current[start_idx:end_idx]))

            normed_error = new_error / (t_range[1] - t_range[0])

            errors.append(normed_error)

        if return_all_errors:
            return errors
        else:
            return sum(errors)

    def get_index_at_tame(self, t):
        return int(round(self.freq * t))

    def get_voltage_at_time(self, t):
        #if t < self.time[0]:
        #    raise Exception('Time is less than allowed')
        #if t > self.time[-1]:
        #    raise Exception('Time is greater than allowed')

        voltage_index = int(round(self.freq * t))

        return self.voltage[voltage_index]

    def get_current_at_time(self, t):
        current_index = int(round(self.freq * t))

        return self.current[current_index]


class PacedTarget(TargetObjective):
    def __init__(self, time, voltage, current, capacitance,
                 protocol_type, target_meta):
        super().__init__(time, voltage, current, capacitance, 
                protocol_type, target_meta)


def create_target_objective(target_meta):
    """
        This function will load in the exp h5 file and create a target obj
        The following adjustements are made to the parameters when the exp
        parametersg
            time (s -> cell_models) – zeroed by subtracting the min time and 
                converted to ms
            voltage (V -> mV) – converted to mV
            current (A -> nA/nF of A/F) – normed by capacitance AND sign
                reversed
    """
    raw_exp_file = File(target_meta.file_path, 'r')

    cm = target_meta.mem_capacitance

    trial_data = get_exp_as_df(raw_exp_file, target_meta.trial, cm)
    
    start_idx = (trial_data['Time (s)'] - 
            target_meta.t_range[0]).abs().idxmin()
    end_idx = (trial_data['Time (s)'] - target_meta.t_range[1]).abs().idxmin()

    start_time = trial_data['Time (s)'].values[start_idx:end_idx].min()

    adjusted_time_range = (trial_data['Time (s)'].values[start_idx:end_idx] -
            start_time) * 1000
    
    adjusted_voltage = trial_data['Voltage (V)'].values[start_idx:end_idx] * 1000

    current = trial_data['Current'].values[start_idx:end_idx]

    if target_meta.protocol_type == 'Voltage Clamp':
        current = remove_capacitive_current(adjusted_time_range,
                                                   adjusted_voltage,
                                                   current)

    if target_meta.protocol_type == 'Dynamic Clamp':
        ishi_name = [name for name in raw_exp_file[f'Trial{target_meta.trial}']
                ['Parameters'].keys() if 'ishi' in name][0]
        ishi_vals = [v for v in raw_exp_file[f'Trial{target_meta.trial}']
                ['Parameters'][ishi_name]]
        max_ishi = max([v[1] for v in ishi_vals])
    else:
        max_ishi = None

    max_current_ranges = {}

    if target_meta.max_current_ranges is not None:
        for current_name, t_range in target_meta.max_current_ranges.items():
            max_current_ranges[current_name] = (
                target_meta.max_current_ranges[current_name] -
                start_time) * 1000

    target = TargetObjective(adjusted_time_range,
                             adjusted_voltage,
                             current,
                             target_meta.protocol_type,
                             target_meta,
                             capacitance=target_meta.mem_capacitance,
                             times_to_compare=max_current_ranges,
                             g_ishi=max_ishi)

    return target


def remove_capacitive_current(time, voltage, current):
    freq = round(1 / (time[1] - time[0]))
    number_capacitive_indices = .6 * freq

    starting_indices = np.argwhere(np.abs(np.diff(voltage)) > 7)
    starting_indices = [s[0] for s in starting_indices]

     
    for index in starting_indices:
        index_range = [index-1, int(index + number_capacitive_indices)]

        if index_range[1] > (len(current) - 1):
            index_range[1] = len(current) - 1

        current_start = current[index_range[0]]
        time_start = time[index_range[0]]

        current_end = current[index_range[1]]
        time_end = time[index_range[1]]

        slope = (current_end - current_start) / (time_end - time_start)

        for index in range(index_range[0], index_range[1]):
            current[index] = current_start + slope * (time[index] - time_start)

    return current


def create_target_from_protocol(cell_model, protocol,
        times_to_compare=None, g_ishi=None, with_ss=False):
    """
    This will create a target object from a cell model and protocol
    """

    is_no_ion_selective = False
    if isinstance(protocol, protocols.AperiodicPacingProtocol):
        proto_type = "Dynamic Clamp"
        is_no_ion_selective = True
    elif isinstance(protocol, protocols.SpontaneousProtocol):
        proto_type = "Spontaneous"
    elif isinstance(protocol, protocols.VoltageClampProtocol):
        proto_type = "Voltage Clamp"

    if with_ss:
        cell_model.find_steady_state(max_iters=2)

    tr = cell_model.generate_response(protocol, is_no_ion_selective=is_no_ion_selective)
    
    if isinstance(cell_model, paci_2018.PaciModel):
        model_name = 'Paci'
        scale = 1000
    elif isinstance(cell_model, kernik.KernikModel):
        model_name = 'Kernik'
        scale = 1
    else:
        print('Model does not exist')

    return TargetObjective(tr.t * scale,
                           tr.y * scale,
                           tr.current_response_info.get_current_summed(),
                           capacitance=cell_model.cm_farad,
                           protocol_type=proto_type,
                           model_name=model_name,
                           target_protocol=protocol,
                           target_meta=None,
                           times_to_compare=times_to_compare)
