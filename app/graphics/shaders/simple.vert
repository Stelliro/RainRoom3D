#version 120
attribute vec3 aPos;
attribute vec3 aNormal;
uniform mat4 uMVP;
uniform mat3 uNrm;
varying vec3 vN;
void main(){
    vN = normalize(uNrm * aNormal);
    gl_Position = uMVP * vec4(aPos, 1.0);
}