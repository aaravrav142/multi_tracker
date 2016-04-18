from optparse import OptionParser
import sys, os
import imp

import rosbag, rospy
import pickle

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui
import pyqtgraph.ptime as ptime
import time
import numpy as np

import read_hdf5_file_to_pandas
import data_slicing

import matplotlib.pyplot as plt

import multi_tracker_analysis as mta
import cv2
import copy

import progressbar
  
pg.mkQApp()
  
## Define main window class from template
path = os.path.dirname(os.path.abspath(__file__))
uiFile = os.path.join(path, 'trajectory_viewer_gui.ui')
WindowTemplate, TemplateBaseClass = pg.Qt.loadUiType(uiFile)
  
def get_filename(path, contains):
    cmd = 'ls ' + path
    ls = os.popen(cmd).read()
    all_filelist = ls.split('\n')
    try:
        all_filelist.remove('')
    except:
        pass
    filelist = []
    for i, filename in enumerate(all_filelist):
        if contains in filename:
            return os.path.join(path, filename)
            
def get_random_color():
    color = (np.random.randint(0,255), np.random.randint(0,255), np.random.randint(0,255))
    return color
  
class QTrajectory(TemplateBaseClass):
    def __init__(self, data_filename, bgimg, delta_video_filename):
        TemplateBaseClass.__init__(self)
        self.setWindowTitle('Trajectory Viewer GUI v2')
  
        # Create the main window
        #self.app = QtGui.QApplication([])
        self.ui = WindowTemplate()
        self.ui.setupUi(self)
        #self.show()

        # Buttons
        self.ui.movie_speed.sliderMoved.connect(self.set_movie_speed)
        self.ui.trajec_undo.clicked.connect(self.trajec_undo)
        self.ui.movie_play.clicked.connect(self.movie_play)
        self.ui.movie_pause.clicked.connect(self.movie_pause)
        self.ui.trajec_delete.clicked.connect(self.toggle_trajec_delete)
        self.ui.trajec_cut.clicked.connect(self.toggle_trajec_cut)
        self.ui.trajec_join_collect.clicked.connect(self.toggle_trajec_join_collect)
        self.ui.trajec_join_add_data.clicked.connect(self.toggle_trajec_join_add_data)
        self.ui.trajec_join_save.clicked.connect(self.trajec_join_save)
        self.ui.trajec_join_clear.clicked.connect(self.toggle_trajec_join_clear)
        self.ui.trajec_save_id.clicked.connect(self.toggle_trajec_save_id)

        # parameters
        self.data_filename = data_filename
        self.load_data()
        self.backgroundimg_filename = bgimg
        self.backgroundimg = None
        self.binsx = None
        self.binsy = None
        trange = np.max(self.pd.time_epoch.values) - np.min(self.pd.time_epoch.values) 
        self.troi = [np.min(self.pd.time_epoch.values), np.min(self.pd.time_epoch.values)+trange*0.1] 
        self.skip_frames = 1
        self.frame_delay = 0.03
        self.path = os.path.dirname(delta_video_filename)
        
        # load delta video bag
        if delta_video_filename != 'none':
            self.dvbag = rosbag.Bag(delta_video_filename)
        else:
            self.dvbag = None
            
        # Initialize 
        try:
            fname = os.path.join(self.path, 'trajec_to_color_dict.pickle')
            f = open(fname, 'r+')
            self.trajec_to_color_dict = pickle.load(f)
            f.close()
            
            fname = os.path.join(self.path, 'trajec_width_dict.pickle')
            f = open(fname, 'r+')
            self.trajec_width_dict = pickle.load(f)
            f.close()
        except:
            self.trajec_to_color_dict = {}
            self.trajec_width_dict = {}
        self.plotted_traces_keys = []
        self.plotted_traces = []
        self.trajectory_ends_vlines = []
        self.data_to_add = []
        
        self.time_mouse_click = time.time()
        self.cut_objects = False
        self.delete_objects = False
        self.join_objects = False
        self.add_data = False
        self.crosshair_pen = pg.mkPen('w', width=1)
        
        self.ui.qtplot_timetrace.enableAutoRange('xy', False)
        if self.config is not None:
            print '**** Sensory stimulus: ', self.config.sensory_stimulus_on
            for r, row in enumerate(self.config.sensory_stimulus_on):
                v1 = pg.PlotDataItem([self.config.sensory_stimulus_on[r][0],self.config.sensory_stimulus_on[r][0]], [0,10])
                v2 = pg.PlotDataItem([self.config.sensory_stimulus_on[r][-1],self.config.sensory_stimulus_on[r][-1]], [0,10])
                f12 = pg.FillBetweenItem(curve1=v1, curve2=v2, brush=pg.mkBrush('r'))
                self.ui.qtplot_timetrace.addItem(f12)
        
        lr = pg.LinearRegionItem(values=self.troi)
        f = 'update_time_region'
        lr.sigRegionChanged.connect(self.__getattribute__(f))
        self.ui.qtplot_timetrace.addItem(lr)
        self.draw_timeseries_vlines_for_interesting_timepoints()
        self.ui.qtplot_timetrace.setRange(xRange=[np.min(self.time_epoch_continuous), np.max(self.time_epoch_continuous)], yRange=[0, np.max(self.nflies)])
        
        self.current_time_vline = pg.InfiniteLine(angle=90, movable=False)
        self.ui.qtplot_timetrace.addItem(self.current_time_vline, ignoreBounds=True)
        self.current_time_vline.setPos(0)
        pen = pg.mkPen((255,255,255), width=2)
        self.current_time_vline.setPen(pen)
        
    ### Button Callbacks
    
    def set_all_buttons_false(self):
        self.cut_objects = False
        self.join_objects = False
        self.delete_objects = False
        self.add_data = False
        self.save_id = False
    
    def set_movie_speed(self, data):
        if data >0:
            self.skip_frames = data
            self.frame_Delay = 0.03
        if data == 0:
            self.skip_frames = 1
            self.frame_delay = 0.03
        if data <0:
            p = 1- (np.abs(data) / 30.)
            max_frame_delay = 0.2
            self.frame_delay = (max_frame_delay - (max_frame_delay*p))*2
    
    def toggle_trajec_save_id(self):
        self.set_all_buttons_false()
        
        self.save_id = True
        self.crosshair_pen = pg.mkPen((248,142,255), width=1)
        print 'Saving objects!'
    
    def trajec_save_id(self, key):
        self.saved_object_id_numbers = os.path.join(self.path, 'save_object_id_numbers.pickle')
        if os.path.exists(self.saved_object_id_numbers):
            f = open(self.saved_object_id_numbers, 'r+')
            data = pickle.load(f)
            f.close()
        else:
            f = open(self.saved_object_id_numbers, 'w+')
            f.close()
            data = []
        data.append(key)
        f = open(self.saved_object_id_numbers, 'r+')
        pickle.dump(data, f)
        f.close()
        
        self.trajec_to_color_dict[key] = (0,0,0)
        self.trajec_width_dict[key] = 3
        self.draw_trajectories()
                
    def save_trajec_color_width_dicts(self):
        fname = os.path.join(self.path, 'trajec_to_color_dict.pickle')
        f = open(fname, 'w+')
        pickle.dump(self.trajec_to_color_dict, f)
        f.close()
        
        fname = os.path.join(self.path, 'trajec_width_dict.pickle')
        f = open(fname, 'w+')
        pickle.dump(self.trajec_width_dict, f)
        f.close()
            
    def trajec_undo(self):
        instruction = self.instructions.pop(-1)
        filename = os.path.join(self.path, 'delete_cut_join_instructions.pickle')
        if os.path.exists(filename):
            f = open(filename, 'r+')
            data = pickle.load(f)
            f.close()
        else:
            f = open(filename, 'w+')
            f.close()
            data = []
        data = self.instructions
        f = open(filename, 'r+')
        pickle.dump(data, f)
        f.close()
        self.load_data()
        self.draw_trajectories()
        self.draw_timeseries_vlines_for_interesting_timepoints()
        
    def movie_pause(self):
        if self.play is True:
            self.play = False
            print 'pause movie'
        elif self.play is False:
            self.play = True
            print 'playing movie'
            self.updateTime = ptime.time()
            self.updateData()
            
    def movie_play(self):
        self.play = True
        print 'loading image sequence'
        self.load_image_sequence()
    
        print 'playing movie'
        self.updateTime = ptime.time()
        self.updateData()
    
    def toggle_trajec_delete(self):
        self.set_all_buttons_false()

        self.delete_objects = True
        self.crosshair_pen = pg.mkPen('r', width=1)
        print 'Deleting objects!'
    
    def toggle_trajec_cut(self):
        self.set_all_buttons_false()

        self.cut_objects = True
        self.crosshair_pen = pg.mkPen('y', width=1)
        print 'Cutting objects!'
    
    def toggle_trajec_join_collect(self):
        self.set_all_buttons_false()
        
        self.join_objects = True
        self.crosshair_pen = pg.mkPen('g', width=1)
        self.object_id_numbers = []
        print 'Ready to collect object id numbers. Click on traces to add object id numbers to the list. Click "save object id numbers" to save, and reset the list'
        
    def toggle_trajec_join_add_data(self):
        self.set_all_buttons_false()

        self.data_to_add = []
        self.add_data = True
        self.crosshair_pen = pg.mkPen((0,0,255), width=1)
        print 'Adding data!'
   
    def toggle_trajec_join_clear(self):
        self.set_all_buttons_false()

        for key in self.object_id_numbers:
            self.trajec_width_dict[key] = 2

        self.crosshair_pen = pg.mkPen('w', width=1)
        self.object_id_numbers = []
        self.add_data = []
        print 'Join list cleared'
        self.draw_trajectories()
    
    ### Mouse moved / clicked callbacks
    
    def mouse_moved(self, pos):
        self.mouse_position = [self.img.mapFromScene(pos).x(), self.img.mapFromScene(pos).y()]
        self.crosshair_vLine.setPos(self.mouse_position[0])
        self.crosshair_hLine.setPos(self.mouse_position[1])
            
        self.crosshair_vLine.setPen(self.crosshair_pen)
        self.crosshair_hLine.setPen(self.crosshair_pen)
        
    def mouse_clicked(self, data):
        self.time_since_mouse_click = time.time() - self.time_mouse_click
        if self.time_since_mouse_click > 0.5:
            if self.add_data:
                self.add_data_to_trajecs_to_join()
        self.time_mouse_click = time.time()
        
    def trace_clicked(self, item): 
        if self.join_objects:
            if item.key not in self.object_id_numbers:
                print 'Saving object to object list: ', item.key
                self.object_id_numbers.append(item.key)
                color = self.trajec_to_color_dict[item.key]
                pen = pg.mkPen(color, width=4)  
                self.trajec_width_dict.setdefault(item.key, 4)
                item.setPen(pen)
            else:
                print 'Removing object from object list: ', item.key
                self.object_id_numbers.remove(item.key)
                color = self.trajec_to_color_dict[item.key]
                pen = pg.mkPen(color, width=2)  
                self.trajec_width_dict.setdefault(item.key, 2)
                item.setPen(pen)
        elif self.cut_objects:
            print 'Cutting trajectory: ', item.key, ' at: ', self.mouse_position
            self.cut_trajectory(item.key, self.mouse_position)
        elif self.delete_objects:
            self.delete_object_id_number(item.key)
        elif self.add_data:
            self.add_data_to_trajecs_to_join()
        elif self.save_id:
            self.trajec_save_id(item.key)
            
    def add_data_to_trajecs_to_join(self):
        self.data_to_add.append([self.current_time_epoch, self.mouse_position[0], self.mouse_position[1]])
        self.draw_data_to_add()
        
    def cut_trajectory(self, key, point):
        dataset = mta.read_hdf5_file_to_pandas.Dataset(self.pd)
        trajec = dataset.trajec(key)
        p = np.vstack((trajec.position_y, trajec.position_x))
        point = np.array([[point[0]], [point[1]]])
        error = np.linalg.norm(p-point, axis=0)
        trajectory_frame = np.argmin(error)
        dataset_frame = dataset.timestamp_to_framestamp(trajec.time_epoch[trajectory_frame])
        
        # now replace objids
        new_objid = np.max(self.pd.objid) + 1
        
        instructions = {'action': 'cut',
                        'order': time.time(),
                        'objid': key,
                        'cut_frame_global': dataset_frame,
                        'cut_frame_trajectory': trajectory_frame, 
                        'cut_time_epoch': trajec.time_epoch[trajectory_frame],
                        'new_objid': new_objid}
        self.save_delete_cut_join_instructions(instructions)

        # update gui
        self.pd = mta.read_hdf5_file_to_pandas.delete_cut_join_trajectories_according_to_instructions(self.pd, instructions, interpolate_joined_trajectories=True)
        self.draw_trajectories()
        self.draw_timeseries_vlines_for_interesting_timepoints()
        
    def trajec_join_save(self):
        instructions = {'action': 'join',
                        'order': time.time(),
                        'objids': self.object_id_numbers,
                        'data_to_add': self.data_to_add}
        print instructions
        self.save_delete_cut_join_instructions(instructions)
        
        self.object_id_numbers = []
        self.data_to_add = []
        self.trajec_width_dict = {}
        
        # now join them for the gui
        self.pd = mta.read_hdf5_file_to_pandas.delete_cut_join_trajectories_according_to_instructions(self.pd, instructions, interpolate_joined_trajectories=True)
        self.draw_trajectories()
        self.draw_timeseries_vlines_for_interesting_timepoints()
        
        print 'Reset object id list - you may collect a new selection of objects now'
        
    def delete_object_id_number(self, key):
        instructions = {'action': 'delete',
                        'order': time.time(),
                        'objid': key}
        self.save_delete_cut_join_instructions(instructions)
        # update gui
        #self.trajec_to_color_dict[key] = (0,0,0,0) 
        self.pd = mta.read_hdf5_file_to_pandas.delete_cut_join_trajectories_according_to_instructions(self.pd, instructions, interpolate_joined_trajectories=True)
        self.draw_trajectories()
        self.draw_timeseries_vlines_for_interesting_timepoints()
    
    ### Drawing functions
    
    def draw_timeseries_vlines_for_interesting_timepoints(self):
        self.calc_time_etc()
        
        # clear
        try:
            self.ui.qtplot_timetrace.removeItem(self.nflies_plot)
        except:
            pass
        for vline in self.trajectory_ends_vlines:
            self.ui.qtplot_timetrace.removeItem(vline)
        # draw
        self.nflies_plot = self.ui.qtplot_timetrace.plot(x=self.time_epoch_continuous, y=self.nflies)
        
        for t in self.pd.groupby('objid').time_epoch.max().values: 
            vline = pg.InfiniteLine(angle=90, movable=False)
            self.ui.qtplot_timetrace.addItem(vline, ignoreBounds=True)
            vline.setPos(t)
            pen = pg.mkPen('g', width=2)
            vline.setPen(pen)
            self.trajectory_ends_vlines.append(vline)
        
        # TODO: times (or frames) where trajectories get very close to one another
    
    def update_time_region(self, linear_region):
        self.linear_region = linear_region
        self.troi = linear_region.getRegion()
        self.draw_trajectories()
        
    def draw_trajectories(self):
        for plotted_trace in self.plotted_traces:
            self.ui.qtplot_trajectory.removeItem(plotted_trace)
        self.ui.qtplot_trajectory.clear()
                
        pd_subset = mta.data_slicing.get_data_in_epoch_timerange(self.pd, self.troi)
        self.dataset = read_hdf5_file_to_pandas.Dataset(self.pd)
        
        if self.binsx is None:
            self.binsx, self.binsy = mta.plot.get_bins_from_backgroundimage(self.backgroundimg_filename)
            self.backgroundimg = cv2.imread(self.backgroundimg_filename, cv2.CV_8UC1)
        img = copy.copy(self.backgroundimg)
        
        # plot a heatmap of the trajectories, for error checking
        h = mta.plot.get_heatmap(pd_subset, self.binsy, self.binsx, position_x='position_y', position_y='position_x', position_z='position_z', position_z_slice=None)
        indices = np.where(h != 0)
        img[indices] = 0
        self.img = pg.ImageItem(img)
        self.ui.qtplot_trajectory.addItem(self.img)
        self.img.setZValue(-200)  # make sure image is behind other data
        
        # cross hair mouse stuff
        self.ui.qtplot_trajectory.scene().sigMouseMoved.connect(self.mouse_moved)
        self.ui.qtplot_trajectory.scene().sigMouseClicked.connect(self.mouse_clicked)
        self.crosshair_vLine = pg.InfiniteLine(angle=90, movable=False)
        self.crosshair_hLine = pg.InfiniteLine(angle=0, movable=False)
        self.ui.qtplot_trajectory.addItem(self.crosshair_vLine, ignoreBounds=True)
        self.ui.qtplot_trajectory.addItem(self.crosshair_hLine, ignoreBounds=True)
        
        keys = np.unique(pd_subset.objid.values)
        self.plotted_traces_keys = []
        self.plotted_traces = []
        if len(keys) < 100:
            for key in keys:
                trajec = self.dataset.trajec(key)
                first_time = np.max([self.troi[0], trajec.time_epoch[0]])
                first_time_index = np.argmin( np.abs(trajec.time_epoch-first_time) )
                last_time = np.min([self.troi[-1], trajec.time_epoch[-1]])
                last_time_index = np.argmin( np.abs(trajec.time_epoch-last_time) )
                #if trajec.length > 5:
                if key not in self.trajec_to_color_dict.keys():
                    color = get_random_color()
                    self.trajec_to_color_dict.setdefault(key, color)
                else:
                    color = self.trajec_to_color_dict[key]
                if key in self.trajec_width_dict.keys():
                    width = self.trajec_width_dict[key]
                else:
                    width = 2
                pen = pg.mkPen(color, width=width)  
                plotted_trace = self.ui.qtplot_trajectory.plot(trajec.position_y[first_time_index:last_time_index], trajec.position_x[first_time_index:last_time_index], pen=pen) 
                self.plotted_traces.append(plotted_trace)
                self.plotted_traces_keys.append(key)
                
            for i, key in enumerate(self.plotted_traces_keys):
                self.plotted_traces[i].curve.setClickable(True, width=3)
                self.plotted_traces[i].curve.key = key
                self.plotted_traces[i].curve.sigClicked.connect(self.trace_clicked)
        
        self.draw_data_to_add()
        self.save_trajec_color_width_dicts()
        
    def draw_data_to_add(self):
        for data in self.data_to_add:
            print data
            self.ui.qtplot_trajectory.plot([data[1]], [data[2]], pen=(0,0,0), symbol='o', symbolSize=10) 
    
    ### Load / read / save data functions
    
    def load_data(self):
        self.pd, self.config = mta.read_hdf5_file_to_pandas.load_and_preprocess_data(self.data_filename)
        self.path = self.config.path
        self.dataset = read_hdf5_file_to_pandas.Dataset(self.pd)
        filename = os.path.join(self.path, 'delete_cut_join_instructions.pickle')
        if os.path.exists(filename):
            f = open(filename, 'r+')
            data = pickle.load(f)
            f.close()
        else:
            data = []
        self.instructions = data
        self.calc_time_etc()
    
    def calc_time_etc(self):
        self.time_epoch = self.pd.time_epoch.groupby(self.pd.index).mean().values
        self.speed = self.pd.speed.groupby(self.pd.index).mean().values
        self.nflies = data_slicing.get_nkeys_per_frame(self.pd)
        self.time_epoch_continuous = np.linspace(np.min(self.time_epoch), np.max(self.time_epoch), len(self.nflies))
        
    def save_delete_cut_join_instructions(self, instructions):
        self.delete_cut_join_filename = os.path.join(self.path, 'delete_cut_join_instructions.pickle')
        if os.path.exists(self.delete_cut_join_filename):
            f = open(self.delete_cut_join_filename, 'r+')
            data = pickle.load(f)
            f.close()
        else:
            f = open(self.delete_cut_join_filename, 'w+')
            f.close()
            data = []
        data.append(instructions)
        f = open(self.delete_cut_join_filename, 'r+')
        pickle.dump(data, f)
        f.close()
        self.instructions.append(instructions)
  
    def load_image_sequence(self):
        timerange = self.troi
        print 'loading image sequence from delta video bag - may take a moment'
        pbar = progressbar.ProgressBar().start()
        
        rt0 = rospy.Time(timerange[0])
        rt1 = rospy.Time(timerange[1])
        self.msgs = self.dvbag.read_messages(start_time=rt0, end_time=rt1)
        
        self.image_sequence = []
        self.image_sequence_timestamps = []
        t0 = None
        for m, msg in enumerate(self.msgs):
            imgcopy = copy.copy(self.backgroundimg)
            imgcopy[ msg[1].xpixels, msg[1].ypixels] = msg[1].values # if there's an error, check if you're using ROS hydro?
            self.image_sequence.append(imgcopy)
            #s = int((m / float(len(self.msgs)))*100)
            tfloat = msg[1].header.stamp.secs + msg[1].header.stamp.nsecs*1e-9
            self.image_sequence_timestamps.append(tfloat)
            if t0 is not None:
                t_elapsed = tfloat - t0
                t_total = timerange[1] - timerange[0]
                s = int(100*(t_elapsed / t_total))
                pbar.update(s)
            else:
                t0 = tfloat
        pbar.finish()
        self.current_frame = -1
        
    def get_next_reconstructed_image(self):
        self.current_frame += self.skip_frames
        if self.current_frame >= len(self.image_sequence)-1:
            self.current_frame = -1
        img = self.image_sequence[self.current_frame]      
        return self.image_sequence_timestamps[self.current_frame], img
    
    def updateData(self):
        if self.play:
            ## Display the data
            time_epoch, cvimg = self.get_next_reconstructed_image()
            self.img.setImage(cvimg)
            
            QtCore.QTimer.singleShot(1, self.updateData)
            now = ptime.time()
            dt = (now-self.updateTime)
            self.updateTime = now
            
            if dt < self.frame_delay:
                d = self.frame_delay - dt
                time.sleep(d)
                
            self.current_time_vline.setPos(time_epoch)
            self.current_time_epoch = time_epoch
            
            del(cvimg)
    
    def run(self):
        ## Display the widget as a new window
        #self.w.show()
        ## Start the Qt event loop
        print 'Running!'
        self.show()
        
## Start Qt event loop unless running in interactive mode or using pyside.
if __name__ == '__main__':
        
    ## Read data #############################################################
    parser = OptionParser()
    parser.add_option('--path', type=str, default='none', help="option: path that points to standard named filename, background image, dvbag, config. If using 'path', no need to provide filename, bgimg, dvbag, and config. Note")
    parser.add_option('--movie', type=int, default=1, help="load and play the dvbag movie, default is 1, to load use 1")
    parser.add_option('--filename', type=str, help="name and path of the hdf5 tracked_objects filename")
    parser.add_option('--bgimg', type=str, help="name and path of the background image")
    parser.add_option('--dvbag', type=str, default='none', help="name and path of the delta video bag file, optional")
    parser.add_option('--config', type=str, default='none', help="name and path of a configuration file, optional. If the configuration file has an attribute 'sensory_stimulus_on', which should be a list of epoch timestamps e.g. [[t1,t2],[t3,4]], then these timeframes will be highlighted in the gui.")
    (options, args) = parser.parse_args()
    
    if options.path != 'none':
        if not os.path.isdir(options.path):
            raise ValueError('Path needs to be a directory!')
        options.filename = get_filename(options.path, 'trackedobjects.hdf5')
        options.config = get_filename(options.path, 'config')
        options.dvbag = get_filename(options.path, 'delta_video.bag')
        options.bgimg = get_filename(options.path, '_bgimg_')
    
    if options.movie is False:
        options.dvbag = 'none'
    
    Qtrajec = QTrajectory(options.filename, options.bgimg, options.dvbag)
    Qtrajec.run()
    
    if (sys.flags.interactive != 1) or not hasattr(QtCore, 'PYQT_VERSION'):
        QtGui.QApplication.instance().exec_()
        
'''

class MainWindow(TemplateBaseClass):  
    def __init__(self):
        TemplateBaseClass.__init__(self)
        self.setWindowTitle('pyqtgraph example: Qt Designer')
  
        # Create the main window
        self.ui = WindowTemplate()
        self.ui.setupUi(self)
        self.ui.movie_play.clicked.connect(self.plot)
  
        self.show()
  
    def plot(self):
        self.ui.qtplot_trajectory.plot(np.random.normal(size=100), clear=True)
  
  
  
## Start Qt event loop unless running in interactive mode or using pyside.
if __name__ == '__main__':
    import sys
    
    win = MainWindow()
    
    if (sys.flags.interactive != 1) or not hasattr(QtCore, 'PYQT_VERSION'):
        QtGui.QApplication.instance().exec_()
'''
