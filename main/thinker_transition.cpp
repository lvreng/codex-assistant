#include "thinker_transition.h"
#include "generated/thinker_camera_assets.h"
#include <algorithm>
#include <cmath>
#include <cstring>

namespace Thinker {
namespace {
constexpr float Pi = 3.14159265359f;
constexpr float ObserverDistance = 16000;
constexpr float SkyDepth = 12000;
constexpr float CenterX = 278, CenterY = 208;
struct Plate { float x, y, radius, extent, world_radius; };
Plate plate(uint8_t model)
{
    switch (model) {
    case 1: return {278,280,258,335,120};
    case 2: return {241,207,167,181,65};
    case 3: return {237,203,161,175,48};
    default: return {};
    }
}
float ease(float t)
{
    if (t<=0) return 0;
    if (t>=1) return 1;
    return std::clamp(t*t*t*(t*(t*6-15)+10),0.0f,1.0f);
}
float window(float a, float b, float t) { return ease((t-a)/(b-a)); }
float lerp(float a, float b, float t) { return a+(b-a)*t; }
Camera preset(uint8_t model)
{
    if (model == 4 || !model) return {{0,0,-ObserverDistance},440,0,0};
    const auto b=plate(model);
    const auto pos=body_position(model);
    const float distance=520*b.world_radius/b.radius;
    return {{pos.x-(b.x-CenterX)*distance/520,
             pos.y-(b.y-CenterY)*distance/520,-distance},520,0,0};
}
struct Basis { Vec3 right, down, forward; };
Basis basis(const Camera &c)
{
    const float cy=std::cos(c.yaw),sy=std::sin(c.yaw);
    const float cp=std::cos(c.pitch),sp=std::sin(c.pitch);
    return {{cy,0,-sy},{sy*sp,cp,cy*sp},{sy*cp,-sp,cy*cp}};
}
float dot(Vec3 a, Vec3 b) { return a.x*b.x+a.y*b.y+a.z*b.z; }

template<class Pixel> Pixel mix(Pixel a, Pixel b, unsigned amount)
{
    const unsigned inv=256-amount;
    if constexpr (sizeof(Pixel)==2) {
        return Pixel(((((a&0xf800u)*inv+(b&0xf800u)*amount)>>8)&0xf800u) |
                     ((((a&0x07e0u)*inv+(b&0x07e0u)*amount)>>8)&0x07e0u) |
                     ((((a&0x001fu)*inv+(b&0x001fu)*amount)>>8)&0x001fu));
    }
    return Pixel(((((a&0xff00ffu)*inv+(b&0xff00ffu)*amount)>>8)&0xff00ffu) |
                 ((((a&0x00ff00u)*inv+(b&0x00ff00u)*amount)>>8)&0x00ff00u) | 0xff000000u);
}
template<class Pixel> Pixel rgb(unsigned r,unsigned g,unsigned b)
{
    if constexpr(sizeof(Pixel)==2) return Pixel(((r>>3)<<11)|((g>>2)<<5)|(b>>3));
    return Pixel(0xff000000u|(r<<16)|(g<<8)|b);
}
template<class Pixel> Pixel backdrop() { return rgb<Pixel>(7,16,25); }
template<class Pixel> float luminance(Pixel p)
{
    if constexpr(sizeof(Pixel)==2)
        return float(std::max({unsigned((p>>11)*8),unsigned(((p>>5)&63)*4),unsigned((p&31)*8)}))/255;
    return float(std::max({unsigned((p>>16)&255),unsigned((p>>8)&255),unsigned(p&255)}))/255;
}
template<class Pixel> Pixel asset(const uint8_t *pixels,int w,int h,int x,int y)
{
    x=std::clamp(x,0,w-1); y=std::clamp(y,0,h-1);
    const auto *p=pixels+(y*w+x)*3;
    return rgb<Pixel>(p[0],p[1],p[2]);
}
template<class Pixel> Pixel asset_linear(const uint8_t *pixels,int w,int h,float x,float y)
{
    const int ix=int(std::floor(x)),iy=int(std::floor(y));
    const unsigned fx=unsigned((x-ix)*256),fy=unsigned((y-iy)*256);
    return mix(mix(asset<Pixel>(pixels,w,h,ix,iy),asset<Pixel>(pixels,w,h,ix+1,iy),fx),
               mix(asset<Pixel>(pixels,w,h,ix,iy+1),asset<Pixel>(pixels,w,h,ix+1,iy+1),fx),fy);
}
template<class Pixel> Pixel linear(const Pixel *pixels,int w,int h,float x,float y)
{
    x=std::clamp(x,0.0f,float(w-1)); y=std::clamp(y,0.0f,float(h-1));
    const int ix=int(x),iy=int(y),nx=std::min(ix+1,w-1),ny=std::min(iy+1,h-1);
    const unsigned fx=unsigned((x-ix)*256),fy=unsigned((y-iy)*256);
    return mix(mix(pixels[iy*w+ix],pixels[iy*w+nx],fx),
               mix(pixels[ny*w+ix],pixels[ny*w+nx],fx),fy);
}

// Inverse pinhole projection of a depth layer. One division per eight-pixel
// span keeps the ESP32 path bounded; it is not independent sprite scaling.
struct Mapping {
    Vec3 u,v,d;
    Mapping(const Camera &view,const Camera &reference,float depth)
    {
        const auto axes=basis(view);
        const float z=depth-view.position.z;
        const float q=reference.focal/(depth-reference.position.z);
        const float offset_x=(view.position.x-reference.position.x)*q+CenterX;
        const float offset_y=(view.position.y-reference.position.y)*q+CenterY;
        const Vec3 rx={axes.right.x/view.focal,axes.down.x/view.focal,axes.forward.x};
        const Vec3 ry={axes.right.y/view.focal,axes.down.y/view.focal,axes.forward.y};
        d={axes.right.z/view.focal,axes.down.z/view.focal,axes.forward.z};
        u={offset_x*d.x+z*q*rx.x,offset_x*d.y+z*q*rx.y,offset_x*d.z+z*q*rx.z};
        v={offset_y*d.x+z*q*ry.x,offset_y*d.y+z*q*ry.y,offset_y*d.z+z*q*ry.z};
    }
    void at(float x,float y,float &sx,float &sy) const
    {
        x-=CenterX; y-=CenterY;
        const float inverse=1/(d.x*x+d.y*y+d.z);
        sx=(u.x*x+u.y*y+u.z)*inverse;
        sy=(v.x*x+v.y*y+v.z)*inverse;
    }
};

template<class Pixel> void sky(const Camera &view,const Pixel *live,int w,int h,Pixel *out)
{
    const Mapping map(view,preset(4),SkyDepth);
    const float scale=h/416.0f, inv=1/scale;
    for (int y=0;y<h;++y) {
        float sx,sy;
        map.at(0,y*inv,sx,sy);
        for (int x=0;x<w;x+=8) {
            const int n=std::min(8,w-x);
            float ex,ey;
            map.at((x+n)*inv,y*inv,ex,ey);
            const float dx=(ex-sx)/n,dy=(ey-sy)/n;
            for (int j=0;j<n;++j,sx+=dx,sy+=dy) {
                // UI labels are not part of the sky. Use the original art there.
                const float clean=window(28,56,sy)*(1-window(310,336,sy))*(1-window(490,540,sx));
                const float tx=std::abs(sx),ty=std::abs(sy);
                Pixel pixel=asset_linear<Pixel>(Assets::Nebula,624,416,
                    tx>623?1246-tx:tx,ty>415?830-ty:ty);
                if (live && clean>0) pixel=mix(pixel,linear(live,w,h,sx*scale,sy*scale),unsigned(clean*256));
                out[y*w+x+j]=pixel;
            }
            sx=ex; sy=ey;
        }
    }
}

template<class Pixel> Pixel surface(const Pixel *image,int w,int h,uint8_t model,float x,float y)
{
    const float scale=h/416.0f;
    const auto live=linear(image,w,h,x*scale,y*scale);
    if (model != 1 || (x-278)*(x-278)+(y-280)*(y-280)>258*258) return live;
    const float available=window(30,66,y)*(1-window(360,416,y));
    if (available>=1) return live;
    const auto packed=linear(Assets::SolarSphere,512,512,(x-278)*255.5f/258+255.5f,(y-280)*255.5f/258+255.5f);
    Pixel hidden;
    if constexpr(sizeof(Pixel)==2) hidden=packed;
    else hidden=rgb<Pixel>(((packed>>11)*255+15)/31,(((packed>>5)&63)*255+31)/63,((packed&31)*255+15)/31);
    return mix(hidden,live,unsigned(available*256));
}

template<class Pixel> void body(const Camera &view,const Pixel *source,Pixel *out,
                                int w,int h,uint8_t model)
{
    if (!model || model==4) return;
    const auto ref=preset(model);
    const auto b=plate(model);
    const auto position=body_position(model);
    const auto screen=project(view,position);
    if (screen.z<=0) return;
    const float scale=h/416.0f, inv=1/scale;
    const float extent=b.extent*b.world_radius/b.radius;
    const float radius=view.focal*extent/screen.z*1.12f;
    const int left=std::max(0,int((screen.x-radius)*scale));
    const int right=std::min(w-1,int((screen.x+radius)*scale+1));
    const int top=std::max(0,int((screen.y-radius)*scale));
    const int bottom=std::min(h-1,int((screen.y+radius)*scale+1));
    const Mapping map(view,ref,0);
    const float feather=model==1?28:10;
    const float edge=b.extent*b.extent, inner=(b.extent-feather)*(b.extent-feather);
    const float projected_radius=view.focal*b.world_radius/screen.z;
    const float resolved=window(.7f,4.5f,projected_radius);
    for (int y=top;y<=bottom;++y) {
        float sx,sy;
        map.at(left*inv,y*inv,sx,sy);
        for (int x=left;x<=right;x+=8) {
            const int n=std::min(8,right-x+1);
            float ex,ey;
            map.at((x+n)*inv,y*inv,ex,ey);
            const float dx=(ex-sx)/n,dy=(ey-sy)/n;
            for (int j=0;j<n;++j,sx+=dx,sy+=dy) {
                const float bx=sx-b.x,by=sy-b.y;
                float opacity=std::clamp((edge-bx*bx-by*by)/(edge-inner),0.0f,1.0f)*resolved;
                if (model!=1) {
                    opacity*=window(0,20,sx)*(1-window(540,560,sx));
                    opacity*=window(0,28,sy)*(1-window(390,416,sy));
                }
                if (opacity<=0) continue;
                const Pixel pixel=surface(source,w,h,model,sx,sy);
                if (model==1) {
                    const float halo=window(254*254,262*262,bx*bx+by*by);
                    const float valid=window(0,64,sy)*(1-window(358,416,sy))*
                        window(0,40,sx)*(1-window(520,560,sx));
                    opacity*=lerp(1,window(.12f,.82f,luminance(pixel))*valid,halo);
                }
                out[y*w+x+j]=mix(out[y*w+x+j],pixel,unsigned(opacity*256));
            }
            sx=ex; sy=ey;
        }
    }
}

template<class Pixel> void stars(const Camera &view,Pixel *out,int w,int h,float strength)
{
    uint32_t seed=0x2c98a731;
    const auto random=[&seed]() { seed=1664525u*seed+1013904223u; return (seed>>8)/16777216.0f; };
    const float scale=h/416.0f;
    for (int i=0;i<90;++i) {
        const float x=32+random()*490,y=38+random()*318;
        const float z=1800+random()*7400;
        const Vec3 world={(x-CenterX)*(z+ObserverDistance)/440,
                          (y-CenterY)*(z+ObserverDistance)/440,z};
        const auto point=project(view,world);
        const float r=scale*std::clamp(.55f*(z+ObserverDistance)/point.z,.55f,1.8f);
        const float px=point.x*scale,py=point.y*scale;
        const Pixel color=i%3==0?rgb<Pixel>(242,195,132):rgb<Pixel>(149,202,234);
        for (int iy=std::max(0,int(py-r-1));iy<std::min(h,int(py+r+2));++iy)
            for (int ix=std::max(0,int(px-r-1));ix<std::min(w,int(px+r+2));++ix) {
                const float dx=(ix-px)/r,dy=(iy-py)/r;
                const float a=std::max(0.0f,1-(dx*dx+dy*dy)*.55f)*strength;
                out[iy*w+ix]=mix(out[iy*w+ix],color,unsigned(a*150));
            }
    }
}

template<class Pixel> void render(const Pixel *a,const Pixel *b,Pixel *out,int w,int h,
                                  uint8_t from,uint8_t to,float p)
{
    if (!a || !b || !out || w<=0 || h<=0) return;
    if (p<=0) { std::memcpy(out,a,size_t(w)*h*sizeof(Pixel)); return; }
    if (p>=1) { std::memcpy(out,b,size_t(w)*h*sizeof(Pixel)); return; }
    if (!from || !to || from==to || from>4 || to>4) {
        const unsigned alpha=unsigned(ease(p)*256);
        for (int i=0;i<w*h;++i) out[i]=mix(a[i],b[i],alpha);
        return;
    }
    const Camera view=camera(from,to,p);
    sky(view,from==4?a:to==4?b:nullptr,w,h,out);
    const float flight=window(0,.2f,p)*(1-window(.8f,1,p));
    stars(view,out,w,h,flight);
    body(view,a,out,w,h,from);
    body(view,b,out,w,h,to);
    const float scale=416.0f/h;
    const unsigned departure=unsigned((1-window(0,.12f,p))*256);
    const unsigned arrival=unsigned(window(.88f,1,p)*256);
    // Register photographed endpoints and keep their UI in screen space.
    // The short fades cover unavailable photo margins, not the camera travel.
    for (int y=0;y<h;++y) {
        const float py=y*scale;
        const float vertical=(1-window(0,38,py))+window(350,416,py)*.88f;
        for (int x=0;x<w;++x) {
            const int i=y*w+x;
            const float px=x*scale;
            const float edge=std::min(1.0f,vertical+window(510,556,px));
            Pixel pixel=mix(out[i],backdrop<Pixel>(),unsigned(edge*256));
            if (departure) pixel=mix(pixel,a[i],departure);
            if (arrival) pixel=mix(pixel,b[i],arrival);
            out[i]=pixel;
        }
    }
}
}

Vec3 body_position(uint8_t model)
{
    const float x=model==1?357:model==2?303:340;
    const float y=model==1?92:model==2?47:152;
    return {(x-CenterX)*ObserverDistance/440,(y-CenterY)*ObserverDistance/440,0};
}

Camera camera(uint8_t from,uint8_t to,float progress)
{
    float p=std::clamp(progress,0.0f,1.0f);
    if (from>to) { std::swap(from,to); p=1-p; }
    const auto first=preset(from),last=preset(to);
    const float q=ease(p),arc=std::sin(Pi*q)*std::sin(Pi*q);
    float distance=std::exp(lerp(std::log(-first.position.z),std::log(-last.position.z),q));
    const bool interplanetary=to!=4;
    if (interplanetary) distance*=std::exp(arc*std::log(ObserverDistance/
        std::sqrt(first.position.z*last.position.z)));
    const float pan=interplanetary?window(.12f,.88f,q):window(.20f,1,p);
    return {{lerp(first.position.x,last.position.x,pan),
             lerp(first.position.y,last.position.y,pan),-distance},
            lerp(first.focal,last.focal,q)-54*arc,
            .045f*std::sin(2*Pi*q)*arc,.023f*arc*std::sin(Pi*q)};
}

Vec3 project(const Camera &camera,Vec3 world)
{
    const auto axes=basis(camera);
    const Vec3 relative={world.x-camera.position.x,world.y-camera.position.y,world.z-camera.position.z};
    const float z=dot(relative,axes.forward);
    return {CenterX+camera.focal*dot(relative,axes.right)/z,
            CenterY+camera.focal*dot(relative,axes.down)/z,z};
}
void render565(const uint16_t *a,const uint16_t *b,uint16_t *out,int w,int h,
               uint8_t from,uint8_t to,float p) { render(a,b,out,w,h,from,to,p); }
void render888(const uint32_t *a,const uint32_t *b,uint32_t *out,int w,int h,
               uint8_t from,uint8_t to,float p) { render(a,b,out,w,h,from,to,p); }
}
