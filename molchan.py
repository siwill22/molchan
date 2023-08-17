import numpy as np
import pandas as pd
import pygmt
from gprm import PointDistributionOnSphere
from collections import OrderedDict
from gprm.utils.proximity import contour_proximity, polyline_proximity, polygons_buffer, reconstruct_and_rasterize_polygons


DEFAULT_DISTANCE_MAX = 1e7
DEFAULT_DISTANCE_STEP = 2e4
DEFAULT_GEOGRAPHIC_EXTENT = [-180.,180.,-90.,90.]
DEFAULT_GEOGRAPHIC_SAMPLING = 0.25



def molchan_test(grid, 
                 points, 
                 distance_max = DEFAULT_DISTANCE_MAX, 
                 distance_step = DEFAULT_DISTANCE_STEP,
                 buffer_radius=1):
    """
    Molchan test for a set of points against one grid
    """
    
    earth_area = (6371.**2)*(4*np.pi)
    
    # distance of each point to the target
    points['distance'] = pygmt.grdtrack(points=pd.DataFrame(data=points[['Longitude','Latitude']]), 
                                        grid=grid, 
                                        no_skip=False, 
                                        interpolation='l',
                                        radius=buffer_radius,
                                        newcolname='dist')['dist']
    
    # percentage of target distance grid within each contour level
    grid_histogram = pygmt.grdvolume(grid, contour=[0, distance_max, distance_step], f='g', unit='k')
    
    (point_histogram,
     bin_edges) = np.histogram(points['distance'], 
                               bins=np.arange(-distance_step, distance_max+distance_step, distance_step))
    
    permissible_area = grid_histogram.iloc[0,1]
    print('Total permissible area is {:0.1f}% of total Earth surface'.format(100*permissible_area/earth_area))
    
    grid_fraction = 1-grid_histogram.iloc[:,1]/permissible_area 
    # Note that this computation will penalize models where there are lots of 
    # invalid points (since they are 'missed' at any grid fraction)
    points_fraction = 1-np.cumsum(point_histogram)/len(points)
    
    Skill = 0.5+np.trapz(grid_fraction, points_fraction)
    
    return grid_fraction[::-1], points_fraction[::-1], Skill



def molchan_point(grid, 
                  points, 
                  distance_max = DEFAULT_DISTANCE_MAX, 
                  distance_step = DEFAULT_DISTANCE_STEP, 
                  buffer_radius=1, 
                  verbose=False):
    """
    Molchan test for a single point
    """
    
    earth_area = (6371.**2)*(4*np.pi)
    
    # distance of each point to the target
    points['distance'] = pygmt.grdtrack(grid=grid,
                                        points=points[['Longitude','Latitude']], 
                                        no_skip=False, 
                                        interpolation='l',
                                        radius=buffer_radius,
                                        newcolname='dist')['dist']
    # percentage of target distance grid within each contour level
    grid_histogram = pygmt.grdvolume(grid, contour=[0, distance_max, distance_step], 
                                     f='g', unit='k', verbose='e')
    
    permissible_area = grid_histogram.iloc[0,1]
    if verbose:
        print('Total permissible area is {:0.1f}% of total Earth surface'.format(100*permissible_area/earth_area))
    
    grid_fraction = 1-grid_histogram.iloc[:,1]/permissible_area 
    
    area_better_than_points = np.interp(points['distance'], grid_histogram.loc[:,0], grid_histogram.loc[:,1])
    
    return float(points['distance'].values), float(1-area_better_than_points/permissible_area)
    
    

def space_time_molchan_test(raster_dict, 
                            point_distances,
                            healpix_resolution=128,
                            distance_max = DEFAULT_DISTANCE_MAX, 
                            distance_step = DEFAULT_DISTANCE_STEP):
    
    hp = PointDistributionOnSphere(distribution_type='healpix', N=128)

    space_time_distances = []

    for reconstruction_time in raster_dict.keys():
        smpl = pygmt.grdtrack(
            grid=raster_dict[reconstruction_time], 
            points=pd.DataFrame(data={'x':hp.longitude, 'y':hp.latitude}), 
            no_skip=False,
            interpolation='l',
            newcolname='distance'
        )

        space_time_distances.extend(smpl['distance'].dropna().tolist())

    # Determine for both the grids and the points, the fraction of overall
    # points within each distance contour
    (hp_histogram,
     bin_edges) = np.histogram(space_time_distances, 
                               bins=np.arange(-distance_step, distance_max+distance_step, distance_step))

    (pm_histogram,
     bin_edges) = np.histogram(point_distances,
                               bins=np.arange(-distance_step, distance_max+distance_step, distance_step))

    grid_fraction = np.cumsum(hp_histogram)/len(space_time_distances)
    point_fraction = 1-np.cumsum(pm_histogram)/len(point_distances)
    
    Skill = np.trapz(1-grid_fraction, 1-point_fraction) - 0.5

    return grid_fraction, point_fraction, Skill



def combine_raster_sequences(raster_dict1, raster_dict2):
    
    raster_dict3 = OrderedDict()

    for key in raster_dict1.keys():
        raster_dict3[key] = raster_dict1[key] * raster_dict2[key]
        
    return raster_dict3

    

def space_time_distances(raster_dict, gdf, age_field_name='age'):
    """
    Computes the distances to targets rconstructed to their time of appearance 
    from a raster sequence of raster grids
    
    The input gdf is assumed to have reconstructed coordinates in its geometry
    """
    
    results = []

    for i,row in gdf.iterrows():
        reconstruction_time = row[age_field_name]
        result = molchan_point(raster_dict[reconstruction_time],
                               pd.DataFrame(data={'Longitude': [row.geometry.x], 
                                                  'Latitude': [row.geometry.y]}))
        results.append(result)

    return pd.DataFrame(data=results, 
                        columns=['distance', 'area_fraction'])



def generate_raster_sequence_from_polygons(features,
                                           rotation_model,
                                           reconstruction_times,
                                           sampling=DEFAULT_GEOGRAPHIC_SAMPLING):
    """
    Given some reconstrutable polygon features, generates a series of 
    """
    
    raster_dict = OrderedDict()

    for reconstruction_time in reconstruction_times:  
        
        tmp = reconstruct_and_rasterize_polygons(features,
                                                 rotation_model,
                                                 reconstruction_time,
                                                 sampling=sampling)

        tmp = tmp.where(tmp!=0, np.nan)
        raster_dict[reconstruction_time] = tmp
        
    return raster_dict



def generate_distance_raster_sequence(target_features, 
                                      reconstruction_model,
                                      reconstruction_times,
                                      sampling=DEFAULT_GEOGRAPHIC_SAMPLING,
                                      region=DEFAULT_GEOGRAPHIC_EXTENT):
    
    prox_grid_sequence = OrderedDict()
    
    for reconstruction_time in reconstruction_times:
        #generate distance raster, masked against the permissive area
        r_target_features = reconstruction_model.reconstruct(target_features, 
                                                             reconstruction_time, 
                                                             use_tempfile=False)

        if r_target_features is not None:
            prox_grid = polyline_proximity(r_target_features,
                                           spacing=sampling, 
                                           region=region)
        else:
            prox_grid = np.ones_like(tmp) * np.nan

        prox_grid_sequence[reconstruction_time] = prox_grid
        
    return prox_grid_sequence


