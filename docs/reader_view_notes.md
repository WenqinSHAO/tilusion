# Reader View Notes

## Known Issue: Timeline Canvas Dragging

Timeline canvas drag-to-pan is currently unreliable across directions and browser/SVG hit layers. Vertical movement may work while horizontal movement can fail or feel uneven.

For now, treat scrollbars, mouse wheel, and trackpad scrolling as the supported navigation path for the structured timeline canvas. Drag-to-pan should be revisited later as a focused UI task, likely by replacing the raw SVG scroll surface with a dedicated pan/zoom viewport abstraction or a small canvas/SVG interaction library.

