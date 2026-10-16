"""State known calibration and the published acquisition metadata explicitly."""

from util.acquisition_record import AcquisitionRecordError, read_record


def source_metadata_summary(source):
    axes = []
    for axis in ('x', 'y'):
        values = {layout.pixel_size_um[axis] for layout in source.layouts}
        value = next(iter(values)) if len(values) == 1 else None
        axes.append(f'{axis.upper()} {value:g} µm/px' if value is not None
                    else f'{axis.upper()} unknown')
    return ('Pixel size: ' + ', '.join(axes) + '. Positions and shared acquisition starts '
            'are checked across source tiles during conversion; preview labels are provisional.')


def recorded_metadata_summary(root):
    try:
        record = read_record(root)
    except (AcquisitionRecordError, OSError) as error:
        return f'Acquisition metadata unavailable: {error}'
    if record is None:
        return 'No acquisition record is present.'
    tiles = record['tiles']
    positions = ', '.join(f'{sum(tile[key] is not None for tile in tiles)}/{len(tiles)} {label}'
                          for key, label in (('mosaic_xy_px', 'mosaic'), ('stage_xy_um', 'stage')))
    starts = sum(data['started_at'] is not None for data in record['timepoints'].values())
    scales = ', '.join(f'{token}: ' + ', '.join(
        f'{axis.upper()} {value:g}' if value is not None else f'{axis.upper()} unknown'
        for axis, value in data['pixel_size_um'].items())
        for token, data in record['timepoints'].items())
    return (f'Recorded pixel size (µm/px): {scales or "none"}. Positions: {positions}. '
            f'Shared starts: {starts}/{len(record["timepoints"])} timepoints. '
            f'{record.get("timepoint_naming", {}).get("reason") or ""}').strip()
