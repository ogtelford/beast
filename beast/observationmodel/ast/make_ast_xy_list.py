from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import numpy as np

from astropy.io import ascii, fits
from astropy.table import Column, Table
from astropy.wcs import WCS

from ...tools.pbar import Pbar


def pick_positions_per_background(chosen_seds, bg_map, N_bg_bins,
                                  outfile=None, refimage=None, Nrealize=1):
    """
    Spreads a set of fake stars across regions of similar background
    density, given a background density map file generated by 'create
    background density map' in the tools directory.

    The tiles of the given background map are divided across a given
    number of background intensity bins. Each background bin will then
    have its own set of tiles, which constitute a region on the image.

    Then, for each background bin, the given set of fake stars is
    duplicated, and the stars are assigned random positions within this
    region.

    This way, it can be ensured that enough ASTs are performed for each
    regime of diffuse background emission, making it possible to have a
    separate noise model for each of these regions.

    Parameters
    ----------

    chosen_seds: astropy Table
        Table containing fake stars to be duplicated and assigned positions

    bg_map: str
        Path to a fits file containing a background map. Each row in the
        fits table should represent a tile of the map. The table should
        have columns describing for each tile: the minimum and maximum
        RA, the minimum and maximum DEC,and a value which represents the
        background density.

    N_bg_bins: int
        The number of bins for the range of background density values.
        The bins will be picked on a linear grid, rangin from the
        minimum to the maximum background value of the map. Then, each
        tile will be put in a bin, so that a set of tiles of the map is
        obtained for each range of background density values.

    refimage: str
        Path to fits image that is used for the positions. If none is
        given, the ra and dec will be put in the x and y output columns
        instead.

    Nrealize: integer
        The number of times each model shoud be repeated for each
        background regime. This is to sample the variance due to
        variations within each region, for each individual model.

    Returns
    -------
    astropy Table: List of fake stars, with magnitudes and positions
    - optionally -
    ascii file of this table, written to outfile

    """

    # Load the background map
    bg = Table.read(bg_map)
    tile_bg_vals = bg['median_bg']
    min_bg = np.amin(tile_bg_vals)
    max_bg = np.amax(tile_bg_vals)

    # Create the background bins
    # [min, ., ., ., max]
    bg_bins = np.linspace(min_bg - 0.01 * abs(min_bg),
                          max_bg + 0.01 * abs(max_bg), N_bg_bins + 1)

    # Find which bin each tile belongs to
    # e.g. one of these numbers: 0 [1, 2, 3, 4, 5] 6
    # We have purposely chosen our bin boundaries so that no points fall
    # outside of the [1,5] range
    bgbin_foreach_tile = np.digitize(tile_bg_vals, bg_bins)
    # Invert this (the [0] is to dereference the tuple (i,) returned by
    # nonzero)
    tiles_foreach_bgbin = [np.nonzero(bgbin_foreach_tile == b + 1)[0]
                           for b in range(N_bg_bins)]

    # Remove empty bins
    tile_sets = [tile_set for tile_set in tiles_foreach_bgbin if len(tile_set)]

    # Repeat the seds Nrealize times (sample each on at Nrealize
    # different positions, in each region)
    repeated_seds = np.repeat(chosen_seds, Nrealize)
    Nseds_per_region = len(repeated_seds)
    # For each set of tiles, repeat the seds and spread them evenly over
    # the tiles
    repeated_seds = np.repeat(repeated_seds, len(tile_sets))

    out_table = Table(repeated_seds, names=chosen_seds.colnames)
    xs = np.zeros(len(out_table))
    ys = np.zeros(len(out_table))
    bin_indices = np.zeros(len(out_table))

    tile_ra_min = bg['min_ra']
    tile_dec_min = bg['min_dec']
    tile_ra_delta = bg['max_ra'] - tile_ra_min
    tile_dec_delta = bg['max_dec'] - tile_dec_min

    if refimage is None:
        wcs = None
    else:
        imagehdu = fits.open(refimage)[1]
        wcs = WCS(imagehdu.header)

    pbar = Pbar(len(tile_sets),
                desc='{} models per background bin'.format(Nseds_per_region))
    for bin_index, tile_set in pbar.iterover(enumerate(tile_sets)):
        start = bin_index * Nseds_per_region
        stop = start + Nseds_per_region
        bin_indices[start:stop] = bin_index
        for i in range(Nseds_per_region):
            x = -1
            y = -1
            # Convert each ra,dec to x,y. If there are negative values, try again
            while x < 0 or y < 0:
                # Pick a random tile
                tile = np.random.choice(tile_set)
                # Within this tile, pick a random ra and dec
                ra = tile_ra_min[tile] + \
                     np.random.random_sample() * tile_ra_delta[tile]
                dec = tile_dec_min[tile] + \
                      np.random.random_sample() * tile_dec_delta[tile]

                if wcs is None:
                    x, y = ra, dec
                    break
                else:
                    [x], [y] = wcs.all_world2pix(np.array([ra]), np.array([dec]), 0)

            j = bin_index * Nseds_per_region + i
            xs[j] = x
            ys[j] = y


    # I'm just mimicking the format that is produced by the examples
    cs = []
    cs.append(Column(np.zeros(len(out_table), dtype=int), name='zeros'))
    cs.append(Column(np.ones(len(out_table), dtype=int), name='ones'))

    if wcs is None:
        cs.append(Column(xs, name='RA'))
        cs.append(Column(ys, name='DEC'))
    else:
        cs.append(Column(xs, name='X'))
        cs.append(Column(ys, name='Y'))

    for i, c in enumerate(cs):
        out_table.add_column(c, index=i)  # insert these columns from the left

    # Write out the table in ascii
    if outfile:
        formats = {k: '%.5f' for k in out_table.colnames[2:]}
        ascii.write(out_table, outfile, overwrite=True, formats=formats)

    return out_table


def pick_positions(catalog, filename, separation, refimage=None):
    """
    Assigns positions to fake star list generated by pick_models

    INPUTS:
    -------

    filename:   string
                Name of AST list generated by pick_models
    separation: float
                Minimum pixel separation between AST and star in photometry 
                catalog provided in the datamodel.
    refimage:   Name of the reference image.  If supplied, the method will use the 
                reference image header to convert from RA and DEC to X and Y.

    OUTPUTS:
    --------

    Ascii table that replaces [filename] with a new version of
    [filename] that contains the necessary position columns for running
    the ASTs though DOLPHOT
    """

    noise = 3.0 #Spreads the ASTs in a circular annulus of 3 pixel width instead of all being 
                #precisely [separation] from an observed star.

    colnames = catalog.data.columns    

    if 'X' or 'x' in colnames:
        if 'X' in colnames:
           x_positions = catalog.data['X'][:]
           y_positions = catalog.data['Y'][:]
        if 'x' in colnames:
           x_positions = catalog.data['x'][:]
           y_positions = catalog.data['y'][:]
    else:
        if refimage:
            if 'RA' or 'ra' in colnames:
                if 'RA' in colnames:
                    ra_positions = catalog.data['RA'][:]
                    dec_positions = catalog.data['DEC'][:]
                if 'ra' in colnames:
                    ra_positions = catalog.data['ra'][:]
                    dec_positions = catalog.data['dec'][:]
            else:
                raise RuntimeError("Your catalog does not supply X, Y or RA, DEC information for spatial AST distribution")

        else:
            raise RuntimeError("You must supply a Reference Image to determine spatial AST distribution.")
        wcs = WCS(refimage)
        x_positions,y_positions = wcs.all_world2pix(ra_positions,dec_positions,0)
 
    astmags = ascii.read(filename)

    n_asts = len(astmags)

    # keep is defined to ensure that no fake stars are put outside of the image boundaries

    keep = (x_positions > np.min(x_positions) + separation + noise) & (x_positions < np.max(x_positions) - separation - noise) & \
           (y_positions > np.min(y_positions) + separation + noise) & (y_positions < np.max(y_positions) - separation - noise)

    x_positions = x_positions[keep]
    y_positions = y_positions[keep]

    ncat = len(x_positions)
    ind = np.random.random(n_asts)*ncat
    ind = ind.astype('int')


    # Here we generate the circular distribution of ASTs surrounding random observed stars
 
    separation = np.random.random(n_asts)*noise + separation
    theta = np.random.random(n_asts) * 2.0 * np.pi
    xvar = separation * np.cos(theta)
    yvar = separation * np.sin(theta)
    
    new_x = x_positions[ind]+xvar; new_y = y_positions[ind]+yvar
    column1 = 0 * new_x
    column2 = column1 + 1
    column1 = Column(name='zeros',data=column1.astype('int'))
    column2 = Column(name='ones',data=column2.astype('int'))
    column3 = Column(name='X',data=new_x,format='%.2f')
    column4 = Column(name='Y',data=new_y,format='%.2f')
    astmags.add_column(column1,0)
    astmags.add_column(column2,1)
    astmags.add_column(column3,2)
    astmags.add_column(column4,3)
    
    ascii.write(astmags,filename,overwrite=True)
    
def pick_positions_per_density(chosen_seds, dens_map, N_dens_bins,
                                  outfile=None, refimage=None, Nrealize=1):
    """
    Spreads a set of fake stars across regions of similar source
    density, given a source density map file generated by 'create
    source density map' in the tools directory.

    The tiles of the given density map are divided across a given
    number of source density bins. Each density bin will then
    have its own set of tiles, which constitute a region on the image.

    Then, for each density bin, the given set of fake stars is
    duplicated, and the stars are assigned random positions within this
    region.

    This way, it can be ensured that enough ASTs are performed for each
    regime of stellar crowding, making it possible to have a
    separate noise model for each of these regions.

    Parameters
    ----------

    chosen_seds: astropy Table
        Table containing fake stars to be duplicated and assigned positions

    dens_map: str
        Path to a fits file containing a density map. Each row in the
        fits table should represent a tile of the map. The table should
        have columns describing for each tile: the minimum and maximum
        RA, the minimum and maximum DEC,and a value which represents the
        source density.

    N_dens_bins: int
        The number of bins for the range of source density values.
        The bins will be picked on a linear grid, rangin from the
        minimum to the maximum density value of the map. Then, each
        tile will be put in a bin, so that a set of tiles of the map is
        obtained for each range of background density values.

    refimage: str
        Path to fits image that is used for the positions. If none is
        given, the ra and dec will be put in the x and y output columns
        instead.

    Nrealize: integer
        The number of times each model shoud be repeated for each
        background regime. This is to sample the variance due to
        variations within each region, for each individual model.

    Returns
    -------
    astropy Table: List of fake stars, with magnitudes and positions
    - optionally -
    ascii file of this table, written to outfile

    """

    # Load the density map
    dens = Table.read(dens_map)
    tile_dens_vals = dens['sourcedens']
    min_dens = np.amin(tile_dens_vals)
    max_dens = np.amax(tile_dens_vals)

    # Create the background bins
    # [min, ., ., ., max]
    dens_bins = np.linspace(min_dens - 0.01 * abs(min_dens),
                          max_dens + 0.01 * abs(max_dens), N_dens_bins + 1)

    # Find which bin each tile belongs to
    # e.g. one of these numbers: 0 [1, 2, 3, 4, 5] 6
    # We have purposely chosen our bin boundaries so that no points fall
    # outside of the [1,5] range
    densbin_foreach_tile = np.digitize(tile_dens_vals, dens_bins)
    # Invert this (the [0] is to dereference the tuple (i,) returned by
    # nonzero)
    tiles_foreach_densbin = [np.nonzero(densbin_foreach_tile == b + 1)[0]
                           for b in range(N_dens_bins)]

    # Remove empty bins
    tile_sets = [tile_set for tile_set in tiles_foreach_densbin if len(tile_set)]

    # Repeat the seds Nrealize times (sample each on at Nrealize
    # different positions, in each region)
    repeated_seds = np.repeat(chosen_seds, Nrealize)
    Nseds_per_region = len(repeated_seds)
    # For each set of tiles, repeat the seds and spread them evenly over
    # the tiles
    repeated_seds = np.repeat(repeated_seds, len(tile_sets))

    out_table = Table(repeated_seds, names=chosen_seds.colnames)
    ras = np.zeros(len(out_table))
    decs = np.zeros(len(out_table))
    bin_indices = np.zeros(len(out_table))

    tile_ra_min = dens['min_ra']
    tile_dec_min = dens['min_dec']
    tile_ra_delta = dens['max_ra'] - tile_ra_min
    tile_dec_delta = dens['max_dec'] - tile_dec_min

    pbar = Pbar(len(tile_sets),
                desc='{} models per background bin'.format(Nseds_per_region))
    for bin_index, tile_set in pbar.iterover(enumerate(tile_sets)):
        start = bin_index * Nseds_per_region
        stop = start + Nseds_per_region
        bin_indices[start:stop] = bin_index
        for i in range(Nseds_per_region):
            j = bin_index * Nseds_per_region + i
            # Pick a random tile
            tile = np.random.choice(tile_set)
            # Within this tile, pick a random ra and dec
            ras[j] = tile_ra_min[tile] + \
                np.random.random_sample() * tile_ra_delta[tile]
            decs[j] = tile_dec_min[tile] + \
                np.random.random_sample() * tile_dec_delta[tile]

    # I'm just mimicking the format that is produced by the examples
    cs = []
    cs.append(Column(np.zeros(len(out_table)), name='zeros'))
    cs.append(Column(np.ones(len(out_table)), name='ones'))

    if refimage is None:
        cs.append(Column(ras, name='RA'))
        cs.append(Column(decs, name='DEC'))
    else:
        imagehdu = fits.open(refimage)[1]
        wcs = WCS(imagehdu.header)
        xs, ys = wcs.all_world2pix(ras, decs, 0)
        cs.append(Column(xs, name='X'))
        cs.append(Column(ys, name='Y'))

    for i, c in enumerate(cs):
        out_table.add_column(c, index=i)  # insert these columns from the left

    # Write out the table in ascii
    if outfile:
        formats = {k: '%.5f' for k in out_table.colnames}
        ascii.write(out_table, outfile, overwrite=True, formats=formats)

    return out_table
