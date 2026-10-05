import paramiko

# SSH (Secure shell): allow to securely access and manage 
# a remote computer or server over an unencrypted network
def optimize_controller(ip, _username, _password, target_channel):
    # Initialize an object (class: SSHClient) to serve as "The Controller".
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        #Initiate connections (Persistent session)
        ssh_client.connect(hostname=ip, username=_username,
                           password=_password,timeout=10)

        # Execute uci (Unified Configuration Interface) to force the router to change its channel
        _command = f"uci set wireless.radio0.channel='{target_channel} && uci commit wireless && wifi reload"
        stdin, stdout, stderr = ssh_client.exec_command(command=_command)
        print(f"Change the router'channel: {target_channel}. Waiting for Association Stations to connect")
    except Exception as e:
        print(f"Communication error: {e}")
    finally:
        # Close connection to free the RAM storage of OpenWrt.
        ssh_client.close()
    
optimize_controller('198.168.1.1','root','my_password','6')
print('Program excecuted!!!')