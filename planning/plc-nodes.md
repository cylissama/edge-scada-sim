# PLC nodes

these simulated PLC nodes should simulate a PLC. These should have certain defined tags that are available to read over network. 

The tags should be able to be user defined for this project. In a file somewhere in JSON format I should be able to edit a config that holds these tags.

say {
    "PLC_ON": 1,
    "ROBOT_ON": 0,
}

somthing like that.

These will live on the "edge" of our k8s cluster or network.