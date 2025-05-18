"""
Contains prompts used by the CAD generation system.
"""

PROMPT_TEMPLATE = """
You are an expert FreeCAD developer. Write a standalone FreeCAD Python script that fulfils the following user request.
The script MUST:
1. Import FreeCAD and needed modules.
2. Build the geometry corresponding to the user description.
3. Save the resulting document to a file called /data/output.FCStd.
Do NOT add explanations or comments outside the python code. Only output valid python code.
User request: {user_prompt}
""" 

ENHANCED_PROMPT_TEMPLATE = """
You are an expert FreeCAD developer. Write a standalone FreeCAD Python script that fulfils the following user request.

# REQUIREMENTS
The script MUST:
1. Import FreeCAD and needed modules (typically Part, math, and sometimes Draft or other workbenches).
2. Create a new document and set it as active if needed.
3. Build the geometry corresponding to the user description using proper FreeCAD methods.
4. Save the resulting document to a file called /data/output.FCStd.
5. Include brief inline comments explaining key operations.
6. Use variables for dimensions to make the design parametric when appropriate.
7. Follow FreeCAD Python scripting best practices.

# FREECAD SCRIPTING GUIDELINES

## Basic Document Setup
```python
import FreeCAD as App
import Part
import math

# Create new document
doc = App.newDocument("CADModel")
App.setActiveDocument("CADModel")
```

## Core Geometry Creation
- Basic shapes: Part.makeBox(), Part.makeCylinder(), Part.makeSphere(), Part.makeCone(), etc.
- 2D geometry: Part.makeLine(), Part.makeCircle(), Part.makePolygon(), etc.
- Curves: Part.BezierCurve(), Part.Arc()
- Wire from edges: Part.Wire([edge1, edge2, ...])
- Face from wire: Part.Face(wire)

## Common Operations
- Boolean operations: shape1.cut(shape2), shape1.fuse(shape2), shape1.common(shape2)
- Transformations: shape.translate(vector), shape.rotate(center, axis, angle), shape.transformShape(matrix)
- Extrusions: face.extrude(vector)
- Fillets: shape.makeFillet(radius, edges)

## Showing and Saving
```python
Part.show(shape)
# At the end of your script:
doc.saveAs("/data/output.FCStd")
```

# EXAMPLES

## Example 1: Basic Part with Hole
```python
import FreeCAD as App
import Part

# Create new document
doc = App.newDocument("HoledBlock")
App.setActiveDocument("HoledBlock")

# Create base box
box = Part.makeBox(100, 60, 20)

# Create cylinder for hole
cylinder = Part.makeCylinder(12, 30, App.Vector(50, 30, -5), App.Vector(0, 0, 1))

# Cut hole through box
result = box.cut(cylinder)

# Add to document
Part.show(result)

# Save the document
doc.saveAs("/data/output.FCStd")
```

## Example 2: Revolved Shape
```python
import FreeCAD as App
import Part
from FreeCAD import Base

# Create new document
doc = App.newDocument("RevolvedPart")
App.setActiveDocument("RevolvedPart")

# Create profile
L1 = Part.makeLine((0, 0, 0), (20, 0, 0))
L2 = Part.makeLine((20, 0, 0), (20, 0, 30))
L3 = Part.makeLine((20, 0, 30), (15, 0, 30))
L4 = Part.makeLine((15, 0, 30), (10, 0, 25))
L5 = Part.makeLine((10, 0, 25), (5, 0, 25))
L6 = Part.makeLine((5, 0, 25), (0, 0, 20))
L7 = Part.makeLine((0, 0, 20), (0, 0, 0))

# Create wire and face
wire = Part.Wire([L1, L2, L3, L4, L5, L6, L7])
face = Part.Face(wire)

# Revolve to create solid
solid = face.revolve(Base.Vector(0, 0, 0), Base.Vector(0, 0, 1), 360)

# Add to document
Part.show(solid)

# Save the document
doc.saveAs("/data/output.FCStd")
```

## Example 3: Assembly with Multiple Parts
```python
import FreeCAD as App
import Part
import math

# Create new document
doc = App.newDocument("Assembly")
App.setActiveDocument("Assembly")

# Base plate
plate = Part.makeBox(100, 100, 10)
Part.show(plate, "Plate")

# Four cylindrical standoffs
for i in range(4):
    angle = i * math.pi/2
    x = 80 * math.cos(angle) + 50
    y = 80 * math.sin(angle) + 50
    cylinder = Part.makeCylinder(5, 30, App.Vector(x, y, 10), App.Vector(0, 0, 1))
    Part.show(cylinder, f"Standoff_{{i}}")

# Top plate with holes
top = Part.makeBox(90, 90, 5, App.Vector(5, 5, 40))
for i in range(4):
    angle = i * math.pi/2
    x = 80 * math.cos(angle) + 50
    y = 80 * math.sin(angle) + 50
    hole = Part.makeCylinder(5.5, 10, App.Vector(x, y, 35), App.Vector(0, 0, 1))
    top = top.cut(hole)
Part.show(top, "TopPlate")

# Save the document
doc.saveAs("/data/output.FCStd")
```

## Example 4: Advanced Techniques (Curves, Fillets, Patterns)
```python
import FreeCAD as App
import Part
import math
from FreeCAD import Base

# Create new document
doc = App.newDocument("AdvancedPart")
App.setActiveDocument("AdvancedPart")

# Define key dimensions as variables
base_width = 100.0
base_height = 20.0
hole_radius = 5.0
fillet_radius = 3.0

# Create base shape
base = Part.makeBox(base_width, base_width, base_height)

# Apply fillets to all vertical edges
edges_to_fillet = []
for edge in base.Edges:
    # Find vertical edges (those parallel to Z axis)
    if abs(edge.tangentAt(0).z) > 0.99:
        edges_to_fillet.append(edge)

base = base.makeFillet(fillet_radius, edges_to_fillet)

# Create a circular pattern of holes
hole_count = 8
center_x = base_width / 2
center_y = base_width / 2
hole_pattern_radius = base_width * 0.35

for i in range(hole_count):
    angle = i * 2 * math.pi / hole_count
    x = center_x + hole_pattern_radius * math.cos(angle)
    y = center_y + hole_pattern_radius * math.sin(angle)
    
    hole = Part.makeCylinder(
        hole_radius, 
        base_height + 10, 
        Base.Vector(x, y, -5), 
        Base.Vector(0, 0, 1)
    )
    base = base.cut(hole)

# Create a rounded arc handle on top
arc_center = Base.Vector(center_x, center_y, base_height)
p1 = Base.Vector(center_x - 20, center_y, base_height)
p2 = Base.Vector(center_x, center_y + 20, base_height + 15)
p3 = Base.Vector(center_x + 20, center_y, base_height)

# Create arc through three points
arc = Part.Arc(p1, p2, p3)
arc_edge = arc.toShape()

# Create a pipe along the arc path
pipe_profile = Part.makeCircle(2.0, Base.Vector(0, 0, 0))
pipe_wire = Part.Wire(pipe_profile)
pipe_face = Part.Face(pipe_wire)

# Create a path for the sweep
path_wire = Part.Wire(arc_edge)

# Sweep the profile along the path
handle = pipe_face.sweep(path_wire)

# Combine base and handle
result = base.fuse(handle)

# Add to document
Part.show(result)

# Save the document
doc.saveAs("/data/output.FCStd")
```

## Example 5: Classic Bottle Example
```python
import FreeCAD as App
import Part
import math

# Create new document
doc = App.newDocument("Bottle")
App.setActiveDocument("Bottle")

# Define parametric dimensions
myWidth = 50.0
myHeight = 70.0
myThickness = 30.0

# Create the base profile points for the bottle
aPnt1 = App.Vector(-myWidth/2, 0, 0)
aPnt2 = App.Vector(-myWidth/2, -myThickness/4, 0)
aPnt3 = App.Vector(0, -myThickness/2, 0)
aPnt4 = App.Vector(myWidth/2, -myThickness/4, 0)
aPnt5 = App.Vector(myWidth/2, 0, 0)

# Create the bottom profile geometry
aArcOfCircle = Part.Arc(aPnt2, aPnt3, aPnt4)
aSegment1 = Part.LineSegment(aPnt1, aPnt2)
aSegment2 = Part.LineSegment(aPnt4, aPnt5)

# Convert geometry to shapes
aEdge1 = aSegment1.toShape()
aEdge2 = aArcOfCircle.toShape()
aEdge3 = aSegment2.toShape()
aWire = Part.Wire([aEdge1, aEdge2, aEdge3])

# Create a mirror transformation matrix
aTrsf = App.Matrix()
aTrsf.rotateZ(math.pi)  # Rotate around Z axis

# Mirror the wire to create the full profile
aMirroredWire = aWire.copy()
aMirroredWire.transformShape(aTrsf)
myWireProfile = Part.Wire([aWire, aMirroredWire])

# Create a face from the wire profile
myFaceProfile = Part.Face(myWireProfile)

# Extrude the face to create the bottle body
aPrismVec = App.Vector(0, 0, myHeight)
myBody = myFaceProfile.extrude(aPrismVec)

# Apply fillets to all edges of the body
myBody = myBody.makeFillet(myThickness/12.0, myBody.Edges)

# Create the neck of the bottle
neckLocation = App.Vector(0, 0, myHeight)
neckNormal = App.Vector(0, 0, 1)
myNeckRadius = myThickness/4.0
myNeckHeight = myHeight/10.0
myNeck = Part.makeCylinder(myNeckRadius, myNeckHeight, neckLocation, neckNormal)

# Fuse the neck with the body
myBottle = myBody.fuse(myNeck)

# Add to document
Part.show(myBottle)

# Create a hollowed version (optional)
# This makes a shell with wall thickness of myThickness/50
myHollowBottle = myBottle.makeThickness(
    [myBottle.Faces[0]], 
    -myThickness/50, 
    1.0e-3
)
Part.show(myHollowBottle, "HollowBottle")

# Save the document
doc.saveAs("/data/output.FCStd")
```

## Example 6: Ball Bearing with Circular Pattern
```python
import FreeCAD as App
import Part
import math
from FreeCAD import Base

# Create new document
doc = App.newDocument("BallBearing")
App.setActiveDocument("BallBearing")

# Define parametric dimensions
R1 = 15.0       # Shaft radius / inner radius of inner ring
R2 = 25.0       # Outer radius of inner ring
R3 = 30.0       # Inner radius of outer ring
R4 = 40.0       # Outer radius of outer ring
TH = 15.0       # Thickness of bearing
NUM_BALLS = 10  # Number of balls
BALL_RADIUS = 5.0  # Radius of each ball
FILLET_RADIUS = 1.0  # Radius for edge fillets

# Calculate ball position
BALL_CENTER_RADIUS = ((R3 - R2) / 2) + R2  # Radial position of ball centers
BALL_Z_POS = TH / 2                       # Z position of ball centers

# Create Inner Ring
inner_cylinder1 = Part.makeCylinder(R1, TH)
inner_cylinder2 = Part.makeCylinder(R2, TH)
inner_ring = inner_cylinder2.cut(inner_cylinder1)

# Apply fillets to inner ring edges
inner_ring = inner_ring.makeFillet(FILLET_RADIUS, inner_ring.Edges)

# Create a torus for the ball groove in the inner ring
torus1 = Part.makeTorus(BALL_CENTER_RADIUS, BALL_RADIUS)
torus1.translate(Base.Vector(0, 0, TH/2))

# Cut the groove from the inner ring
inner_ring = inner_ring.cut(torus1)
Part.show(inner_ring, "InnerRing")

# Create Outer Ring
outer_cylinder1 = Part.makeCylinder(R3, TH)
outer_cylinder2 = Part.makeCylinder(R4, TH)
outer_ring = outer_cylinder2.cut(outer_cylinder1)

# Apply fillets to outer ring edges
outer_ring = outer_ring.makeFillet(FILLET_RADIUS, outer_ring.Edges)

# Create a torus for the ball groove in the outer ring
torus2 = Part.makeTorus(BALL_CENTER_RADIUS, BALL_RADIUS)
torus2.translate(Base.Vector(0, 0, TH/2))

# Cut the groove from the outer ring
outer_ring = outer_ring.cut(torus2)
Part.show(outer_ring, "OuterRing")

# Create the balls in a circular pattern
for i in range(NUM_BALLS):
    # Calculate angle for ball placement
    angle = (i * 2 * math.pi) / NUM_BALLS
    
    # Calculate position based on angle
    x = BALL_CENTER_RADIUS * math.cos(angle)
    y = BALL_CENTER_RADIUS * math.sin(angle)
    
    # Create ball
    ball = Part.makeSphere(BALL_RADIUS)
    ball.translate(Base.Vector(x, y, BALL_Z_POS))
    
    # Add ball to document
    Part.show(ball, f"Ball_{{i}}")

# Set view options for better visualization
doc.recompute()

# Save the document
doc.saveAs("/data/output.FCStd")
```

Do NOT add explanations or comments outside the Python code. Only output valid Python code that satisfies the requirements.

User request: {user_prompt}
""" 