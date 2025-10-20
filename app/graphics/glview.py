
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL.GL import *
from OpenGL.GLU import gluPerspective, gluLookAt
import math, random, time

PICK_NONE = ("none", -1)
MODE_MOVE, MODE_ROT, MODE_SCALE = 0,1,2

class GLRoomView(QOpenGLWidget):
    cameraChanged = QtCore.Signal(float,float,float)
    selectionChanged = QtCore.Signal(tuple)
    requestRepaint = QtCore.Signal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setMinimumSize(900,600)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.yaw=0.7; self.pitch=0.35; self.distance=8.0
        self.room=None; self.roof_type="Flat"
        self._t0=time.time()
        self._rain_particles=[]; self._splashes=[]
        self._max_particles=1100; self._wind_scale=2.0
        self._selection=PICK_NONE; self._dragging=False; self._mode=MODE_MOVE
        self.setMouseTracking(True)
        self._timer=QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    # ---------- Public API ----------
    def set_room(self, room):
        self.room = room
        self._init_particles()
        self.update()

    def set_roof(self, name):
        self.roof_type = name
        self.update()

    def set_transform_mode(self, mode):
        self._mode = int(mode)
        self.update()

    # ---------- GL ----------
    def initializeGL(self):
        glEnable(GL_DEPTH_TEST); glEnable(GL_CULL_FACE); glCullFace(GL_BACK)
        glClearColor(0.05,0.06,0.07,1.0)
        glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    def resizeGL(self,w,h):
        glViewport(0,0,w,h)

    def _apply_perspective(self):
        glMatrixMode(GL_PROJECTION); glLoadIdentity()
        gluPerspective(55.0, self.width()/max(1,self.height()), 0.05, 400.0)
        glMatrixMode(GL_MODELVIEW)

    def _apply_cam(self):
        glLoadIdentity()
        cx=self.distance*math.sin(self.yaw)*math.cos(self.pitch)
        cy=self.distance*math.sin(self.pitch)
        cz=self.distance*math.cos(self.yaw)*math.cos(self.pitch)
        gluLookAt(cx,cy,cz,  0,1.2,0,  0,1,0)

    # ---------- Helpers ----------
    def _screen_ray(self, x, y):
        w, h = self.width(), self.height()
        nx = (2.0*x/w - 1.0)
        ny = 1.0 - (2.0*y/h)
        fov = math.radians(55.0)
        tan = math.tan(fov*0.5)
        cam = (
            self.distance*math.sin(self.yaw)*math.cos(self.pitch),
            self.distance*math.sin(self.pitch),
            self.distance*math.cos(self.yaw)*math.cos(self.pitch),
        )
        forward = (-math.sin(self.yaw)*math.cos(self.pitch),
                   -math.sin(self.pitch),
                   -math.cos(self.yaw)*math.cos(self.pitch))
        right = (math.cos(self.yaw), 0.0, -math.sin(self.yaw))
        up = (0.0, 1.0, 0.0)
        dir_world = (
            forward[0] + right[0]*nx*tan + up[0]*ny*tan,
            forward[1] + right[1]*nx*tan + up[1]*ny*tan,
            forward[2] + right[2]*nx*tan + up[2]*ny*tan,
        )
        L = math.sqrt(sum(d*d for d in dir_world))+1e-6
        rd = tuple(d/L for d in dir_world)
        return cam, rd

    # ---------- Scene drawing ----------
    def _draw_sky(self):
        w,h=self.width(), self.height()
        glDisable(GL_DEPTH_TEST)
        glMatrixMode(GL_PROJECTION); glPushMatrix(); glLoadIdentity(); glOrtho(0,w,0,h,-1,1)
        glMatrixMode(GL_MODELVIEW); glPushMatrix(); glLoadIdentity()
        glBegin(GL_QUADS)
        glColor4f(0.10,0.16,0.22,1); glVertex2f(0,h); glVertex2f(w,h)
        glColor4f(0.04,0.06,0.09,1); glVertex2f(w,0); glVertex2f(0,0)
        glEnd()
        glMatrixMode(GL_MODELVIEW); glPopMatrix()
        glMatrixMode(GL_PROJECTION); glPopMatrix()
        glEnable(GL_DEPTH_TEST)

    def _draw_terrain(self):
        if not self.room: return
        R = max(self.room.width, self.room.depth) * 10.0
        glColor3f(0.12,0.12,0.13)
        glBegin(GL_QUADS)
        glVertex3f(-R,0,-R); glVertex3f(R,0,-R); glVertex3f(R,0,R); glVertex3f(-R,0,R)
        glEnd()
        # splashes
        now=time.time()
        glLineWidth(1.5)
        for (x,z,t0) in list(self._splashes):
            age=now-t0
            if age>0.6:
                self._splashes.remove((x,z,t0)); continue
            r=0.05+age*0.6; a=max(0,0.55*(1-age/0.6))
            glColor4f(0.75,0.85,1.0,a)
            glBegin(GL_LINE_LOOP)
            for i in range(32):
                ang=2*math.pi*i/32; glVertex3f(x+r*math.cos(ang),0.005,z+r*math.sin(ang))
            glEnd()

    def _draw_box(self,w,h,d,th=0.12):
        x0,z0=-w/2,-d/2; x1,z1=w/2,d/2
        t=min(th, min(w,d)*0.1)
        ix0,iz0=x0+t,z0+t; ix1,iz1=x1-t,z1-t
        glColor3f(0.22,0.22,0.26)
        glBegin(GL_QUADS)
        # floor
        glVertex3f(x0,0,z0); glVertex3f(x1,0,z0); glVertex3f(x1,0,z1); glVertex3f(x0,0,z1)
        # +Z wall outer/inner
        glVertex3f(x0,0,z1); glVertex3f(x1,0,z1); glVertex3f(x1,h,z1); glVertex3f(x0,h,z1)
        glVertex3f(ix1,0,iz1); glVertex3f(ix0,0,iz1); glVertex3f(ix0,h,iz1); glVertex3f(ix1,h,iz1)
        # -Z wall outer/inner
        glVertex3f(x1,0,z0); glVertex3f(x0,0,z0); glVertex3f(x0,h,z0); glVertex3f(x1,h,z0)
        glVertex3f(ix0,0,iz0); glVertex3f(ix1,0,iz0); glVertex3f(ix1,h,iz0); glVertex3f(ix0,h,iz0)
        # +X wall outer/inner
        glVertex3f(x1,0,z1); glVertex3f(x1,0,z0); glVertex3f(x1,h,z0); glVertex3f(x1,h,z1)
        glVertex3f(ix1,0,iz0); glVertex3f(ix1,0,iz1); glVertex3f(ix1,h,iz1); glVertex3f(ix1,h,iz0)
        # -X wall outer/inner
        glVertex3f(x0,0,z0); glVertex3f(x0,0,z1); glVertex3f(x0,h,z1); glVertex3f(x0,h,z0)
        glVertex3f(ix0,0,iz1); glVertex3f(ix0,0,iz0); glVertex3f(ix0,h,iz0); glVertex3f(ix0,h,iz1)
        glEnd()

    def _draw_windows(self):
        if not self.room: return
        glColor4f(0.2,0.9,0.9,0.6)
        for win in self.room.windows:
            w=self.room.width; d=self.room.depth; h=self.room.height
            x=win.x-w/2; z=win.z-d/2; y0=h*0.6-win.height/2; y1=y0+win.height
            glBegin(GL_QUADS)
            if abs(x)>abs(z):
                sx=-w/2 if x<0 else w/2
                glVertex3f(sx,y0,z); glVertex3f(sx,y1,z); glVertex3f(sx,y1,z+win.width); glVertex3f(sx,y0,z+win.width)
            else:
                sz=-d/2 if z<0 else d/2
                glVertex3f(x,y0,sz); glVertex3f(x+win.width,y1,sz); glVertex3f(x+win.width,y0,sz); glVertex3f(x,y1,sz)
            glEnd()

    def _draw_speakers(self):
        glColor3f(0.7,0.55,0.2)
        for s in self.room.speakers:
            self._draw_cube(s.x-self.room.width/2, s.y, s.z-self.room.depth/2, 0.12)

    def _draw_headphones(self):
        glColor3f(0.2,0.6,0.9)
        for hp in self.room.headphones_items:
            self._draw_sphere(hp.x-self.room.width/2, hp.y, hp.z-self.room.depth/2, 0.12)

    def _draw_cube(self,x,y,z,r):
        glBegin(GL_QUADS)
        for _ in range(6):
            glVertex3f(x-r,y-r,z-r); glVertex3f(x+r,y-r,z-r); glVertex3f(x+r,y+r,z-r); glVertex3f(x-r,y+r,z-r)
        glEnd()

    def _draw_sphere(self,x,y,z,r):
        stacks,slices=10,16
        for i in range(stacks):
            lat0=math.pi*(-0.5+i/stacks); z0=math.sin(lat0); zr0=math.cos(lat0)
            lat1=math.pi*(-0.5+(i+1)/stacks); z1=math.sin(lat1); zr1=math.cos(lat1)
            glBegin(GL_QUAD_STRIP)
            for j in range(slices+1):
                lng=2*math.pi*j/slices; x1=math.cos(lng); y1=math.sin(lng)
                glVertex3f(x+r*x1*zr0, y+r*y1*zr0, z+r*z0)
                glVertex3f(x+r*x1*zr1, y+r*y1*zr1, z+r*z1)
            glEnd()

    def _draw_rain(self):
        if not self._rain_particles: return
        glLineWidth(1.1); glColor4f(0.75,0.85,1.0,0.35)
        glBegin(GL_LINES)
        for (x,y,z,vy,typ) in self._rain_particles:
            glVertex3f(x,y,z); glVertex3f(x,y-0.12,z)
        glEnd()

    def _draw_selection_ring(self):
        if self._selection==PICK_NONE or not self.room: return
        k,i=self._selection
        if k=="speaker":
            x=self.room.speakers[i].x-self.room.width/2; z=self.room.speakers[i].z-self.room.depth/2
        elif k=="headphones":
            x=self.room.headphones_items[i].x-self.room.width/2; z=self.room.headphones_items[i].z-self.room.depth/2
        else:
            x=(self.room.windows[i].x+min(self.room.width,self.room.windows[i].x+self.room.windows[i].width))/2-self.room.width/2
            z=self.room.windows[i].z-self.room.depth/2
        r=0.25; glLineWidth(2); glColor3f(0.6,0.8,1)
        glBegin(GL_LINE_LOOP)
        for n in range(32):
            a=2*math.pi*n/32; glVertex3f(x+r*math.cos(a),0.01,z+r*math.sin(a))
        glEnd()
        glBegin(GL_LINES); glColor3f(1,0,0); glVertex3f(x,0.02,z); glVertex3f(x+0.6,0.02,z); glColor3f(0,1,0); glVertex3f(x,0.02,z); glVertex3f(x,0.02,z+0.6); glEnd()

    # ---------- Rain simulation ----------
    def _spawn_world_rain(self):
        if not self.room: return []
        w,h,d=self.room.width,self.room.height,self.room.depth
        R=max(w,d)*5.0
        count=int(self._max_particles*0.7*max(0.1,self.room.rain_intensity))
        drops=[]
        for _ in range(count):
            px=random.uniform(-R,R); pz=random.uniform(-R,R); py=random.uniform(h+0.5,h+4.0); vy=random.uniform(-3.0,-5.0)
            drops.append([px,py,pz,vy,1])
        return drops

    def _spawn_window_ingress(self):
        if not self.room: return []
        w,h,d=self.room.width,self.room.height,self.room.depth
        drops=[]
        for win in self.room.windows:
            if getattr(win,"open",0.0) <= 0.05: continue
            x=win.x-w/2; z=win.z-d/2; y0=h*0.6-win.height/2
            cx,cz=(x,(d/2 if z>=0 else -d/2)) if abs(x)<=abs(z) else ((w/2 if x>=0 else -w/2), z)
            n=int(12*win.open)
            for _ in range(n):
                px=cx+random.uniform(-0.2,0.2); pz=cz+random.uniform(-0.2,0.2); py=random.uniform(y0+0.1,y0+win.height-0.1); vy=random.uniform(-2.5,-3.5)
                if abs(x)>abs(z): px+=(-0.15 if x>0 else 0.15)
                else: pz+=(-0.15 if z>0 else 0.15)
                drops.append([px,py,pz,vy,2])
        return drops

    def _init_particles(self):
        self._rain_particles=[]
        if not self.room: return
        self._rain_particles=self._spawn_world_rain()
        self._rain_particles.extend(self._spawn_window_ingress())

    def _tick(self):
        if not self.room: return
        w,h,d=self.room.width,self.room.height,self.room.depth
        wind=self.room.wind*self._wind_scale
        R=max(w,d)*5.0
        new=[]
        for p in self._rain_particles:
            p[1]+=p[3]*0.016
            p[0]+=wind*0.016
            if p[1] <= 0.0:
                self._splashes.append((p[0],p[2],time.time()))
                if p[4]==1:
                    # respawn
                    p[0]=random.uniform(-R,R); p[2]=random.uniform(-R,R); p[1]=h+random.uniform(0.5,4.0); p[3]=random.uniform(-3.0,-5.0); new.append(p)
                # else drop dies
            else:
                new.append(p)
        self._rain_particles=new
        target=int(self._max_particles*0.7*max(0.1,self.room.rain_intensity))
        world=[d for d in self._rain_particles if d[4]==1]
        if len(world)<target:
            self._rain_particles.extend(self._spawn_world_rain()[:target-len(world)])
        self._rain_particles.extend(self._spawn_window_ingress())
        self.update()

    # ---------- Interaction ----------
    def mousePressEvent(self,e):
        self.last=e.position()
        if e.button()==QtCore.Qt.LeftButton:
            self._dragging=True
            self._try_select(e.position())

    def mouseMoveEvent(self,e):
        if e.buttons() & QtCore.Qt.RightButton and self.last:
            dx=(e.position().x()-self.last.x())*0.01; dy=(e.position().y()-self.last.y())*0.01
            self.yaw+=dx; self.pitch=max(-1.2, min(1.2, self.pitch+dy))
            self.last=e.position(); self.cameraChanged.emit(self.yaw,self.pitch,self.distance); self.update()
        elif (e.buttons() & QtCore.Qt.LeftButton) and self._dragging and self._selection != PICK_NONE:
            self._apply_transform_drag(e.position())

    def mouseReleaseEvent(self,e):
        self._dragging=False

    def wheelEvent(self,e):
        self.distance = max(1.5, min(50.0, self.distance*(0.9 if e.angleDelta().y()>0 else 1.1)))
        self.cameraChanged.emit(self.yaw,self.pitch,self.distance); self.update()

    def keyPressEvent(self,e):
        if e.key()==QtCore.Qt.Key_W: self.set_transform_mode(MODE_MOVE)
        if e.key()==QtCore.Qt.Key_E: self.set_transform_mode(MODE_ROT)
        if e.key()==QtCore.Qt.Key_R: self.set_transform_mode(MODE_SCALE)

    def _apply_transform_drag(self,pos):
        ro,rd=self._screen_ray(pos.x(),pos.y())
        if abs(rd[1])<1e-6: return
        t=(0.0-ro[1])/rd[1]; hx=ro[0]+rd[0]*t; hz=ro[2]+rd[2]*t
        kind, idx = self._selection
        x = hx + self.room.width/2; z = hz + self.room.depth/2
        if kind=="speaker" and 0<=idx<len(self.room.speakers):
            if self._mode==MODE_MOVE: self.room.speakers[idx].x, self.room.speakers[idx].z = x, z
        elif kind=="headphones" and 0<=idx<len(self.room.headphones_items):
            if self._mode==MODE_MOVE: self.room.headphones_items[idx].x, self.room.headphones_items[idx].z = x, z
        elif kind=="window" and 0<=idx<len(self.room.windows):
            if self._mode==MODE_MOVE:
                self.room.windows[idx].x, self.room.windows[idx].z = x, z
            elif self._mode==MODE_SCALE:
                w = max(0.2, abs(self.room.windows[idx].x - x) + 0.5)
                self.room.windows[idx].width = min(max(0.4, w), min(self.room.width, self.room.depth))
        self.update(); self.requestRepaint.emit()

    def _try_select(self,pos):
        ro,rd=self._screen_ray(pos.x(),pos.y())
        if abs(rd[1])<1e-6:
            self._selection=PICK_NONE; self.selectionChanged.emit(self._selection); return
        t=(0.0-ro[1])/rd[1]; hx=ro[0]+rd[0]*t; hz=ro[2]+rd[2]*t
        sel=PICK_NONE; best=0.5
        if self.room:
            for i,s in enumerate(self.room.speakers):
                sx=s.x-self.room.width/2; sz=s.z-self.room.depth/2
                d=math.hypot(hx-sx, hz-sz)
                if d<best: best=d; sel=("speaker", i)
            for i,hp in enumerate(self.room.headphones_items):
                sx=hp.x-self.room.width/2; sz=hp.z-self.room.depth/2
                d=math.hypot(hx-sx, hz-sz)
                if d<best: best=d; sel=("headphones", i)
            for i,w in enumerate(self.room.windows):
                sx=(w.x+min(self.room.width,w.x+w.width))/2-self.room.width/2; sz=w.z-self.room.depth/2
                d=math.hypot(hx-sx, hz-sz)
                if d<best: best=d; sel=("window", i)
        self._selection=sel; self.selectionChanged.emit(sel); self.update()
