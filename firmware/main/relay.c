// SPDX-License-Identifier: MIT
// Bounded, opaque TCP/UDP transport. No AirPlay decryption or frame buffers.
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <errno.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "lwip/netdb.h"
#include "lwip/tcp.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_heap_caps.h"
#include "esp_mac.h"
#include "mdns.h"
#include "airbridge.h"

#define MAX_PAYLOAD 4096
#define MAX_CHANNELS 8
#define MAX_LISTENERS 6
enum {HELLO=1,CONFIG,OPEN,DATA,CLOSE,PING,PONG,HID,STATUS,UDP_OPEN,ERROR_FRAME,ACK};
typedef struct {int fd,id,port; bool udp;} listener_t;
typedef struct {
    int fd,listener; uint16_t id;
    bool udp,shared,pending,eof;
    struct sockaddr_in peer;
    uint8_t *first_datagram;
    size_t first_length;
} channel_t;
static listener_t listeners[MAX_LISTENERS];
static channel_t channels[MAX_CHANNELS];
static int tunnel=-1,nlisteners;
static uint16_t next_id=1;
static uint64_t bytes_up,bytes_down;
static uint8_t rxbuf[MAX_PAYLOAD+1],txbuf[MAX_PAYLOAD+1];
static uint32_t phone_ip;
// CLOSE and DATA can cross on the two directions of the TCP tunnel.
// Keep bounded tombstones instead of resetting every other live channel.
static uint16_t closed_ids[MAX_CHANNELS*4];
static size_t closed_next;
static uint32_t late_data, transport_reconnects;
static const char *TAG="relay";

static int exact(int fd,void *ptr,size_t size,bool writing) {
    uint8_t *p=ptr;
    while(size) {
        int n=writing?send(fd,p,size,0):recv(fd,p,size,0);
        if(n<0&&errno==EINTR)continue;
        if(n<=0)return -1;
        p+=n;size-=n;
    }
    return 0;
}
static bool frame(uint8_t kind,uint16_t id,const void *data,size_t len) {
    if(len>MAX_PAYLOAD)return false;
    uint8_t h[8]={1,kind,(uint8_t)(id>>8),(uint8_t)id,0,0,(uint8_t)(len>>8),(uint8_t)len};
    return exact(tunnel,h,8,true)==0&&(!len||exact(tunnel,(void *)data,len,true)==0);
}
static bool json_frame(uint8_t kind,uint16_t id,cJSON *j) {
    char *s=cJSON_PrintUnformatted(j); if(!s)return false;
    bool ok=frame(kind,id,s,strlen(s));free(s);return ok;
}
static bool ack(uint16_t id,bool ok) {
    const char *s=ok?"{\"ok\":true}":"{\"ok\":false,\"error\":\"device rejected request\"}";
    return frame(ACK,id,s,strlen(s));
}
static void timeouts(int fd) {
    struct timeval tv={2,0};
    setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&tv,sizeof(tv));
    setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv));
    int type=0; socklen_t size=sizeof(type);
    if(getsockopt(fd,SOL_SOCKET,SO_TYPE,&type,&size)==0 && type==SOCK_STREAM) {
        int one=1;setsockopt(fd,IPPROTO_TCP,TCP_NODELAY,&one,sizeof(one));
    }
}
static void drop(channel_t *ch) {
    if(ch->id)closed_ids[closed_next++%(MAX_CHANNELS*4)]=ch->id;
    if(ch->id && !ch->shared && ch->fd>=0)close(ch->fd);
    free(ch->first_datagram);
    memset(ch,0,sizeof(*ch));ch->fd=-1;
}
static channel_t *find(uint16_t id) {
    for(int i=0;i<MAX_CHANNELS;i++)if(channels[i].id==id)return &channels[i];
    return NULL;
}
static channel_t *allocate(uint16_t id) {
    if(!id||find(id))return NULL;
    for(int i=0;i<MAX_CHANNELS;i++)if(!channels[i].id) {
        for(int k=0;k<MAX_CHANNELS*4;k++)if(closed_ids[k]==id)closed_ids[k]=0;
        channels[i].id=id;channels[i].fd=-1;return &channels[i];
    }
    return NULL;
}
static int integer(cJSON *j,const char *name,int low,int high) {
    cJSON *v=cJSON_GetObjectItemCaseSensitive(j,name);
    return cJSON_IsNumber(v)&&v->valuedouble==v->valueint&&v->valueint>=low&&v->valueint<=high?v->valueint:-1;
}
static const char *string(cJSON *j,const char *name) {
    cJSON *v=cJSON_GetObjectItemCaseSensitive(j,name);
    return cJSON_IsString(v)?v->valuestring:NULL;
}
static bool set_config(cJSON *j) {
    cJSON *array=cJSON_GetObjectItemCaseSensitive(j,"listeners"),*item;
    if(!cJSON_IsArray(array)||cJSON_GetArraySize(array)>MAX_LISTENERS)return false;
    cJSON_ArrayForEach(item,array) {
        int id=integer(item,"id",1,65535),port=integer(item,"port",1,65535);
        const char *proto=string(item,"proto");
        if(id<0||port<0||!proto||(strcmp(proto,"tcp")&&strcmp(proto,"udp")))return false;
        bool udp=!strcmp(proto,"udp");
        for(int k=0;k<nlisteners;k++)if(listeners[k].id==id||
           (listeners[k].port==port&&listeners[k].udp==udp))return false;
        int fd=socket(AF_INET,udp?SOCK_DGRAM:SOCK_STREAM,0);
        if(fd<0){ESP_LOGW(TAG,"Listener socket allocation failed: %d",errno);return false;}
        int one=1;setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&one,sizeof(one));timeouts(fd);
        struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons(port),.sin_addr.s_addr=htonl(INADDR_ANY)};
        if(bind(fd,(struct sockaddr *)&addr,sizeof(addr))||(!udp&&listen(fd,2))) {
            ESP_LOGW(TAG,"Listener bind/listen failed: port=%d errno=%d",port,errno);
            close(fd);return false;
        }
        listeners[nlisteners++]=(listener_t){fd,id,port,udp};
    }
    mdns_service_remove_all();
    cJSON *services=cJSON_GetObjectItemCaseSensitive(j,"mdns");
    if(services&&!cJSON_IsArray(services))return false;
    if(cJSON_GetArraySize(services)>2)return false;
    cJSON_ArrayForEach(item,services) {
        const char *type=string(item,"type"),*name=string(item,"instance");
        int port=integer(item,"port",1,65535);
        cJSON *txt=cJSON_GetObjectItemCaseSensitive(item,"txt"),*kv;
        mdns_txt_item_t entries[24];size_t n=0;
        if(!type||!name||port<0||strlen(name)>63||
           (strcmp(type,"_airplay")&&strcmp(type,"_raop"))||!cJSON_IsObject(txt))return false;
        cJSON_ArrayForEach(kv,txt) {
            if(n>=24||!cJSON_IsString(kv)||!kv->string||strlen(kv->string)+strlen(kv->valuestring)>253)return false;
            entries[n++]=(mdns_txt_item_t){kv->string,kv->valuestring};
        }
        if(mdns_service_add(name,type,"_tcp",port,entries,n)!=ESP_OK)return false;
    }
    ESP_LOGI(TAG,"Configured %d listeners; bounded payload=%d",nlisteners,MAX_PAYLOAD);
    return true;
}
static bool open_client(listener_t *l,int fd,struct sockaddr_in *addr,bool shared) {
    if(phone_ip&&phone_ip!=addr->sin_addr.s_addr) {if(!shared)close(fd);return true;}
    if(next_id>=32768)next_id=1;
    channel_t *ch=allocate(next_id++);
    if(!ch){if(!shared)close(fd);return true;}
    phone_ip=addr->sin_addr.s_addr;
    ch->fd=fd;ch->listener=l->id;ch->udp=l->udp;ch->peer=*addr;ch->shared=shared;ch->pending=true;
    char ip[INET_ADDRSTRLEN];inet_ntop(AF_INET,&addr->sin_addr,ip,sizeof(ip));
    cJSON *j=cJSON_CreateObject();cJSON_AddNumberToObject(j,"listener",l->id);
    cJSON_AddStringToObject(j,"peer",ip);cJSON_AddNumberToObject(j,"port",ntohs(addr->sin_port));
    bool ok=json_frame(OPEN,ch->id,j);cJSON_Delete(j);return ok;
}
static bool handle(uint8_t kind,uint16_t id,size_t len) {
    rxbuf[len]=0;
    if(kind==PING)return frame(PONG,id,rxbuf,len);
    if(kind==PONG)return true;
    if(kind==DATA) {
        channel_t *ch=find(id);
        if(!ch) {
            for(int k=0;k<MAX_CHANNELS*4;k++)if(closed_ids[k]==id){late_data++;return true;}
            ESP_LOGW(TAG,"Data on unknown channel %u",id);return false;
        }
        int n;
        if(ch->shared)n=sendto(ch->fd,rxbuf,len,0,(struct sockaddr *)&ch->peer,sizeof(ch->peer));
        else if(ch->udp)n=send(ch->fd,rxbuf,len,0);
        else n=exact(ch->fd,rxbuf,len,true)==0?(int)len:-1;
        if(n!=(int)len) {frame(CLOSE,id,NULL,0);drop(ch);}
        else bytes_down+=len;
        return true;
    }
    if(kind==CLOSE) {
        channel_t *ch=find(id);
        if(ch) {if(!ch->udp)shutdown(ch->fd,SHUT_WR);drop(ch);}
        return true;
    }
    if(kind==ACK) {
        channel_t *ch=find(id);if(!ch)return true;
        cJSON *j=cJSON_Parse((char *)rxbuf);
        bool ok=cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(j,"ok"));cJSON_Delete(j);
        if(ok) {
            ch->pending=false;
            if(ch->first_datagram) {
                ok=frame(DATA,ch->id,ch->first_datagram,ch->first_length);
                if(ok)bytes_up+=ch->first_length;
                free(ch->first_datagram);ch->first_datagram=NULL;ch->first_length=0;
            }
        } else drop(ch);
        if(!ok && ch->id)return false;
        return true;
    }
    cJSON *j=cJSON_Parse((char *)rxbuf);if(!j)return false;
    bool ok=false;
    if(kind==CONFIG) {
        ok=set_config(j);
        if(!ok)ESP_LOGW(TAG,"Backend configuration rejected");
    }
    else if(kind==HID) {ok=ack(id,hid_command(j));}
    else if(kind==UDP_OPEN) {
        const char *ip=string(j,"peer");int port=integer(j,"port",1,65535);
        struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons(port)};
        bool valid=ip&&port>0&&inet_pton(AF_INET,ip,&addr.sin_addr)==1&&phone_ip==addr.sin_addr.s_addr&&id>=32768;
        channel_t *ch=valid?allocate(id):NULL;
        bool opened=false;
        if(ch) {
            ch->fd=socket(AF_INET,SOCK_DGRAM,0);ch->udp=true;ch->peer=addr;
            if(ch->fd>=0) {timeouts(ch->fd);opened=connect(ch->fd,(struct sockaddr *)&addr,sizeof(addr))==0;}
            if(!opened)drop(ch);
        }
        ok=ack(id,opened);
    }
    cJSON_Delete(j);return ok;
}
static void cleanup(void) {
    hid_release();
    for(int i=0;i<MAX_CHANNELS;i++)drop(&channels[i]);
    for(int i=0;i<nlisteners;i++)close(listeners[i].fd);
    nlisteners=0;phone_ip=0;
    mdns_service_remove_all();
    if(tunnel>=0)close(tunnel);
    tunnel=-1;
}
static int dial(void) {
    struct addrinfo hints={.ai_family=AF_INET,.ai_socktype=SOCK_STREAM},*res=NULL;
    char port[8];snprintf(port,sizeof(port),"%u",bridge_config.port);
    if(getaddrinfo(bridge_config.host,port,&hints,&res))return -1;
    int fd=socket(AF_INET,SOCK_STREAM,0);
    if(fd>=0) {
        timeouts(fd);
        if(connect(fd,res->ai_addr,res->ai_addrlen)){close(fd);fd=-1;}
    }
    freeaddrinfo(res);return fd;
}
static bool run(void) {
    uint8_t mac[6];esp_read_mac(mac,ESP_MAC_WIFI_STA);
    char device[32];snprintf(device,sizeof(device),"esp32c3-%02x%02x%02x",mac[3],mac[4],mac[5]);
    cJSON *j=cJSON_CreateObject();cJSON_AddStringToObject(j,"device",device);cJSON_AddStringToObject(j,"token",bridge_config.token);
    bool ok=json_frame(HELLO,0,j);cJSON_Delete(j);if(!ok)return false;
    int64_t last_status=0,last_rx=esp_timer_get_time();
    while(bridge_wifi_ready) {
        int64_t now=esp_timer_get_time();
        if(now-last_rx>45000000)return false;
        if(now-last_status>10000000) {
            int active=0;for(int k=0;k<MAX_CHANNELS;k++)active+=channels[k].id!=0;
            bool link,encrypted,authenticated;hid_link_state(&link,&encrypted,&authenticated);
            char s[512];snprintf(s,sizeof(s),"{\"firmware\":\"0.2.0\",\"heap\":%lu,\"min_heap\":%lu,\"largest_heap\":%lu,\"ble\":%s,\"ble_link\":%s,\"ble_encrypted\":%s,\"ble_authenticated\":%s,\"up\":%llu,\"down\":%llu,\"uptime_ms\":%lld,\"channels\":%d,\"late_data\":%lu,\"reconnects\":%lu,\"reset_reason\":%d}",
                (unsigned long)esp_get_free_heap_size(),(unsigned long)esp_get_minimum_free_heap_size(),
                (unsigned long)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT),
                hid_connected()?"true":"false",link?"true":"false",encrypted?"true":"false",authenticated?"true":"false",bytes_up,bytes_down,now/1000,active,
                (unsigned long)late_data,(unsigned long)transport_reconnects,(int)esp_reset_reason());
            if(!frame(STATUS,0,s,strlen(s))||!frame(PING,0,NULL,0))return false;
            last_status=now;
        }
        fd_set rf;FD_ZERO(&rf);FD_SET(tunnel,&rf);int maxfd=tunnel;
        for(int i=0;i<nlisteners;i++){FD_SET(listeners[i].fd,&rf);if(listeners[i].fd>maxfd)maxfd=listeners[i].fd;}
        for(int i=0;i<MAX_CHANNELS;i++) {
            channel_t *ch=&channels[i];
            if(ch->id&&!ch->shared&&!ch->pending&&!ch->eof) {FD_SET(ch->fd,&rf);if(ch->fd>maxfd)maxfd=ch->fd;}
        }
        struct timeval tv={0,100000};int selected=select(maxfd+1,&rf,NULL,NULL,&tv);
        if(selected<0){ESP_LOGW(TAG,"select failed: %d",errno);return false;}
        if(FD_ISSET(tunnel,&rf)) {
            uint8_t h[8];if(exact(tunnel,h,8,false)){ESP_LOGW(TAG,"Tunnel header read failed: %d",errno);return false;}
            uint32_t len=((uint32_t)h[4]<<24)|((uint32_t)h[5]<<16)|((uint32_t)h[6]<<8)|h[7];
            if(h[0]!=1||len>MAX_PAYLOAD||exact(tunnel,rxbuf,len,false))return false;
            if(!handle(h[1],((uint16_t)h[2]<<8)|h[3],len))return false;
            last_rx=esp_timer_get_time();
        }
        for(int i=0;i<nlisteners;i++) {
            listener_t *l=&listeners[i];if(!FD_ISSET(l->fd,&rf))continue;
            struct sockaddr_in addr;socklen_t n=sizeof(addr);
            if(!l->udp) {
                int fd=accept(l->fd,(struct sockaddr *)&addr,&n);
                if(fd>=0){timeouts(fd);if(!open_client(l,fd,&addr,false))return false;}
            } else {
                int got=recvfrom(l->fd,txbuf,sizeof(txbuf),0,(struct sockaddr *)&addr,&n);
                if(got<0||got>MAX_PAYLOAD)continue;
                channel_t *found=NULL;
                for(int k=0;k<MAX_CHANNELS;k++)if(channels[k].id&&channels[k].shared&&
                    channels[k].listener==l->id&&channels[k].peer.sin_addr.s_addr==addr.sin_addr.s_addr&&
                    channels[k].peer.sin_port==addr.sin_port){found=&channels[k];break;}
                if(!found) {
                    if(!open_client(l,l->fd,&addr,true))return false;
                    for(int k=0;k<MAX_CHANNELS;k++)if(channels[k].id&&channels[k].shared&&
                        channels[k].listener==l->id&&channels[k].peer.sin_addr.s_addr==addr.sin_addr.s_addr&&
                        channels[k].peer.sin_port==addr.sin_port){found=&channels[k];break;}
                    if(found) {
                        found->first_datagram=malloc(got?got:1);
                        if(!found->first_datagram)return false;
                        memcpy(found->first_datagram,txbuf,got);found->first_length=got;
                    }
                    continue;
                }
                if(!found->pending) {if(!frame(DATA,found->id,txbuf,got))return false;bytes_up+=got;}
            }
        }
        for(int i=0;i<MAX_CHANNELS;i++) {
            channel_t *ch=&channels[i];
            if(!ch->id||ch->shared||ch->pending||ch->eof||!FD_ISSET(ch->fd,&rf))continue;
            int got=recv(ch->fd,txbuf,ch->udp?sizeof(txbuf):MAX_PAYLOAD,0);
            if(got>MAX_PAYLOAD)continue;
            if(got>0){if(!frame(DATA,ch->id,txbuf,got))return false;bytes_up+=got;}
            else if(got==0||!(errno==EAGAIN||errno==EWOULDBLOCK||errno==EINTR)) {
                if(!frame(CLOSE,ch->id,NULL,0))return false;
                if(ch->udp)drop(ch);else ch->eof=true;
            }
        }
        bool any=false;for(int i=0;i<MAX_CHANNELS;i++)any|=channels[i].id!=0;
        if(!any)phone_ip=0;
    }
    return false;
}
void relay_task(void *arg) {
    uint8_t mac[6];char hostname[40];esp_read_mac(mac,ESP_MAC_WIFI_STA);
    snprintf(hostname,sizeof(hostname),"c3-airbridge-%02x%02x%02x",mac[3],mac[4],mac[5]);
    ESP_ERROR_CHECK(mdns_init());ESP_ERROR_CHECK(mdns_hostname_set(hostname));
    for(;;) {
        if(bridge_wifi_ready) {
            tunnel=dial();
            if(tunnel>=0){ESP_LOGI(TAG,"Backend transport connected");run();cleanup();transport_reconnects++;ESP_LOGI(TAG,"Transport closed; retrying");}
        }
        vTaskDelay(pdMS_TO_TICKS(3000));
    }
}
