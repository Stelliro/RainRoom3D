#version 120
varying vec3 vN;
uniform vec3 uLightDir;
uniform vec3 uBaseColor;
void main(){
    float NdotL = max(dot(normalize(vN), normalize(uLightDir)), 0.0);
    vec3 col = uBaseColor * (0.15 + 0.85*NdotL);
    gl_FragColor = vec4(col, 1.0);
}