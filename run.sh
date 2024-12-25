docker build . -t img
docker run -it \
    -v $PWD:/app \
    -v $PWD/logs:/app/logs \
    img python sheeprl.py exp=ppo env=gym env.id=CartPole-v1




