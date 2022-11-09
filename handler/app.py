from datetime import datetime
from distutils.command.upload import upload
from urllib import response
import boto3
from io import BytesIO
from PIL import Image, ImageOps
import os
import uuid
import json

s3 = boto3.client('s3')
bucket_name = str(os.getenv('BUCKET_NAME'))
size = int(os.getenv('THUMBNAIL_SIZE'))
dbtable = str(os.getenv('DYNAMODB_TABLE'))
aws_region = str(os.getenv('REGION_NAME'))
dynamodb = boto3.resource('dynamodb', region_name=aws_region)

#region Lambda functions

#Generates thumbnail on uploading .png to S3
def s3_thumbnail_generator(event, context):
    print("Event::", event)

    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']
    img_size = event['Records'][0]['s3']['object']['size']

    print(bucket_name)

    original_image_url = f"https://{bucket}.s3.{aws_region}.amazonaws.com/{key}"

    if (not key.endswith("_thumbnail.png")):

        image = get_s3_image(bucket, key)

        thumbnail = image_to_thumbnail(image)

        thumbnail_key = new_filename(key)

        url = upload_to_s3(bucket, thumbnail_key, thumbnail, img_size, original_image_url)

        return url

#Returns all items in DynamoDB
def s3_get_thumbnail_urls(event, context):
    # get all image urls from the db and show in a json format
    table = dynamodb.Table(dbtable)
    response = table.scan()
    data = response['Items']
    # paginate through the results in a loop
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        data.extend(response['Items'])

    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(data)
    }

#Gets info of specific item based on ID
def s3_get_item(event, context):

    table = dynamodb.Table(dbtable)
    response = table.get_item(Key={
        'id': event['pathParameters']['id']
    })

    item = response['Item']

    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'},
        'body': json.dumps(item),
        'isBase64Encoded': False,
    }

#Update the name of the entry in DB from ID
def s3_update_thumbnail_name(event, context):
    if ('body' not in event or
            event['httpMethod'] != 'PUT'):
        return {
            'statusCode': 400,
            'headers': {},
            'body': json.dumps({'msg': 'Bad Request'})
        }

    item_id = event['pathParameters']['id']
    item_name = event['pathParameters']['itemName']
    table = dynamodb.Table(dbtable)
    current_timestamp = datetime.now().isoformat()
    # post = json.loads(event['body'])

    params = {
        'id': item_id
    }
    print('PARAMS::>>>>>', params)

    response = table.update_item(
        Key=params,
        UpdateExpression="set itemName=:n, updatedAt=:u",
        ExpressionAttributeValues={
            # ':n': post['itemName'],
            ':n': item_name,
            ':u': str(current_timestamp)
        },
        ReturnValues="UPDATED_NEW"
    )
    print('::====>', response)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Post updated successfully!",
        }),
    }

#Deletes specific entry from DB and S3 (both thumbnail and original) based on ID
def s3_delete_item(event, context):
    item_id = event['pathParameters']['id']
    print(item_id)
    
    # Set the default error response
    response = {
        "statusCode": 500,
        "body": f"An error occured while deleting post {item_id}"
    }

    table = dynamodb.Table(dbtable)

    #Getting key of thumbnail and image to delete
    s3_item = table.get_item(Key={'id': item_id})
    thumbnail_url =  s3_item['Item']['thumbnail_url']
    thumbnail_key = thumbnail_url.rsplit('/', 1)[-1]
    image_url =  s3_item['Item']['image_url']
    image_key = image_url.rsplit('/', 1)[-1]

    #Deleting thumbnail from s3 and dynamoDB
    delete_item_from_s3(image_key)
    delete_item_from_s3(thumbnail_key)

    response = table.delete_item(Key={'id': item_id})

    all_good_response = {
        "deleted": True,
        "itemDeletedId": item_id
    }

   # If deletion is successful for post
    if response['ResponseMetadata']['HTTPStatusCode'] == 200:
        response = {
            "statusCode": 200,
            'headers': {'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'},
            'body': json.dumps(all_good_response),
        }
    return response

#endregion

#region Internal functions

def get_s3_image(bucket, key):
    response = s3.get_object(Bucket=bucket, Key=key)
    imagecontent = response['Body'].read()

    file = BytesIO(imagecontent)
    img = Image.open(file)
    return img

def image_to_thumbnail(image):
    return ImageOps.fit(image, (size, size), Image.ANTIALIAS)

def new_filename(key):
    key_split = key.rsplit('.', 1)
    return key_split[0] + "_thumbnail.png"

def upload_to_s3(bucket, key, image, img_size, original_url):
    # We're saving the image into a BytesIO object to avoid writing to disk
    out_thumbnail = BytesIO()

    # You MUST specify the file type because there is no file name to discern
    # it from
    image.save(out_thumbnail, 'PNG')
    out_thumbnail.seek(0)

    response = s3.put_object(
        ACL='public-read',
        Body=out_thumbnail,
        Bucket=bucket,
        ContentType='image/png',
        Key=key
    )
    print(response)

    url = '{}/{}/{}'.format(s3.meta.endpoint_url, bucket, key)

    # save image url to db:
    s3_save_thumbnail_url_to_dynamo(url_path=url, original_url=original_url, img_size=img_size, img_key=key)

    return url

def s3_save_thumbnail_url_to_dynamo(url_path, original_url, img_size, img_key):
    toint = float(img_size*0.53)/1000
    table = dynamodb.Table(dbtable)
    response = table.put_item(
        Item={
            'id': str(uuid.uuid4()),
            'itemName': str(img_key.rsplit('_', 1)[0]),
            'image_url': str(original_url),
            'thumbnail_url': str(url_path),
            'approxReducedSize': str(toint) + str('KB'),
            'createdAt': str(datetime.now()),
            'updatedAt': str(datetime.now())
        }
    )

# get all image urls from the bucked and show in a json format
    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(response)
    }

def delete_item_from_s3(filename):
    response = s3.delete_object(Bucket=bucket_name, Key=filename)
    print(response)

#endregion