
// ripple.glsl — Circle ripple from impact events (height-only, sample-friendly)
//
// Uniforms:
//   uniform float uTime;             // seconds
//   uniform vec2  uResolution;       // viewport or texture size
//   uniform int   uEventCount;
//   uniform sampler2D uEventsTex;    // packed events: RGBA32F rows = [time, x, z, radius]
//   uniform float uFade = 1.5;       // seconds to fade ripple
//   uniform float uSpeed = 1.2;      // ripple expansion speed (units/sec)
//   uniform float uFreq  = 22.0;     // rings per unit distance
//
// For simplicity, assume (x,z) are normalized 0..1. Map to world in your vertex shader.
//
// Fragment shader pseudo (GLSL 330 / WebGL2 style)
#version 300 es
precision highp float;
out vec4 fragColor;

uniform float uTime;
uniform vec2  uResolution;
uniform int   uEventCount;
uniform sampler2D uEventsTex;
uniform float uFade;
uniform float uSpeed;
uniform float uFreq;

vec4 readEvent(int i) {
    // Events are packed row-wise into uEventsTex, width = 1, height = N
    vec2 uv = vec2(0.5, (float(i)+0.5)/float(textureSize(uEventsTex,0).y));
    return texture(uEventsTex, uv); // time, x, z, radius
}

void main() {
    vec2 uv = gl_FragCoord.xy / uResolution.xy;
    float h = 0.0;
    for (int i = 0; i < 512; ++i) {
        if (i >= uEventCount) break;
        vec4 e = readEvent(i);
        float t = uTime - e.x;
        if (t < 0.0 || t > uFade) continue;
        vec2 pos = vec2(e.y, e.z);
        float r = length(uv - pos);
        float wavefront = t * uSpeed;
        // ring envelope: peak near the wavefront, fall off away from it
        float k = abs(r - wavefront);
        float ring = exp(-k * 40.0) * sin(2.0*3.14159*uFreq*r);
        float fade = 1.0 - (t/uFade);
        h += ring * fade * e.w;
    }
    // Height to grayscale (for a height map), centered at 0.5
    fragColor = vec4(vec3(0.5 + h), 1.0);
}
