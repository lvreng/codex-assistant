#include "model_test.h"
#include "pet_protocol.h"
#include <cstdlib>
#include <iostream>

void check(bool value) { if (!value) std::abort(); }
void packet(Pet::Receiver &receiver,uint8_t command,uint8_t option)
{
    uint8_t bytes[] = {0xc0,0xde,'P','T',2,0,1,0,command,option,0,0};
    const uint16_t crc=Pet::crc16(bytes+4,6);
    bytes[10]=crc&255; bytes[11]=crc>>8;
    for (auto byte:bytes) receiver.feed(byte);
}
int main()
{
    Thinker::ModelTest test;
    check(test.model(0)==0);
    test.start(100);
    for (int model=1;model<=4;++model) {
        const int64_t now=100+(model-1)*test.StepUs;
        check(test.model(now)==model);
        test.start(now+1); // Repeated commands cannot prolong the sequence.
        check(test.model(now+test.StepUs-1)==model);
    }
    check(test.model(100+4*test.StepUs)==0);
    test.start(30000000);
    check(test.model(30000000)==1);
    test.stop();
    check(test.model(30000001)==0);
    Pet::Receiver receiver;
    packet(receiver,6,1);
    check(receiver.good==1 && receiver.is_model_test() && receiver.model_key()==1);
    packet(receiver,6,2);
    check(receiver.good==1 && receiver.bad==1);
    packet(receiver,6,0);
    check(receiver.good==2 && receiver.model_key()==0);
    packet(receiver,5,4);
    check(receiver.good==3 && receiver.is_model() && receiver.model_key()==4);
    std::cout << "Model test sequence and wire controls passed\n";
}
