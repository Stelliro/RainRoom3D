#version 120
// Procedural sky with FBM clouds and day/night tint.
// Not true volumetrics, but layered noise for a soft "volumetric" look.
varying vec2 vUv;
uniform float uTime;        // seconds
uniform float uTOD;         // 0..24 hours
uniform vec2  uWind;        // x,z wind projected to screen
uniform float uCloudiness;  // 0..1
uniform float uStorm;       // 0..1
uniform float uLightning;   // 0..1 (flash strength)

// --- Hash/Noise helpers ---
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453); }
float noise(vec2 p){
    vec2 i = floor(p);
    vec2 f = fract(p);
    float a = hash(i);
    float b = hash(i+vec2(1.0,0.0));
    float c = hash(i+vec2(0.0,1.0));
    float d = hash(i+vec2(1.0,1.0));
    vec2 u = f*f*(3.0-2.0*f);
    return mix(a,b,u.x) + (c-a)*u.y*(1.0-u.x) + (d-b)*u.x*u.y;
}
float fbm(vec2 p){
    float v = 0.0;
    float a = 0.5;
    for(int i=0;i<5;i++){
        v += a*noise(p);
        p *= 2.02;
        a *= 0.55;
    }
    return v;
}

// --- Day/night gradients ---
vec3 daySky(float h){
    // warm horizon, cooler zenith
    vec3 horizon = vec3(0.65, 0.75, 0.95);
    vec3 zenith  = vec3(0.15, 0.30, 0.65);
    return mix(horizon, zenith, h);
}
vec3 duskSky(float h){
    vec3 horizon = vec3(0.95, 0.55, 0.40);
    vec3 zenith  = vec3(0.10, 0.12, 0.25);
    return mix(horizon, zenith, h);
}
vec3 nightSky(float h){
    vec3 horizon = vec3(0.02, 0.03, 0.06);
    vec3 zenith  = vec3(0.00, 0.01, 0.03);
    return mix(horizon, zenith, h);
}

void main(){
    // Height (0 at bottom, 1 at top)
    float h = 1.0 - vUv.y;

    // Time-of-day blend: 0..24. Map to day/dusk/night
    float t = uTOD;
    float dayAmt  = smoothstep(6.0, 10.0, t) * (1.0 - smoothstep(16.0, 19.0, t));
    float duskAmt = smoothstep(17.0, 20.0, t) * (1.0 - smoothstep(20.0, 22.0, t));
    float nightAmt= 1.0 - clamp(dayAmt + duskAmt, 0.0, 1.0);

    vec3 col = daySky(h)*dayAmt + duskSky(h)*duskAmt + nightSky(h)*nightAmt;

    // Clouds: FBM warped by wind and time; more dense with uCloudiness/uStorm
    vec2 p = vUv * vec2(3.0, 1.6);
    p += uWind * (uTime*0.03);
    float n = fbm(p) * 1.2 - 0.25;
    n += 0.25*fbm(p*2.3 + 7.1);
    float clouds = smoothstep(0.35, 0.7, n + uCloudiness*0.6 + uStorm*0.4);

    // Darken clouds more in storm
    vec3 cloudCol = mix(vec3(1.0), vec3(0.12,0.14,0.17), 0.6 + 0.4*uStorm);
    col = mix(col, cloudCol, clouds * (0.35 + 0.55*uCloudiness));

    // Lightning flash overlay
    if(uLightning > 0.001){
        float flash = smoothstep(0.0, 1.0, uLightning);
        col += vec3(1.0, 0.95, 0.88) * flash * (0.35 + 0.65*uStorm);
    }

    gl_FragColor = vec4(col, 1.0);
}
